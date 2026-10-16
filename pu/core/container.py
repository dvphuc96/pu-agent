"""Application composition root.

The composition root owns infrastructure selection and wires durable persistence,
AgentOps, tool/security/approval boundaries, specialized agents and the research
workflow without leaking those choices into domain agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pu.adapters import GraphWorkflowEngine
from pu.approval import ApprovedActionExecutor, HumanApprovalService
from pu.config import Settings, load_settings
from pu.core.interfaces.agents import AgentRuntime
from pu.core.interfaces.evaluation import EvaluationHarness
from pu.core.interfaces.events import EventPublisher
from pu.core.interfaces.llm import LLMProvider
from pu.core.interfaces.orchestration import AgentOrchestrator
from pu.core.interfaces.repositories import MemoryRepository
from pu.core.interfaces.security import SecurityHarness
from pu.core.interfaces.tools import ToolHarness
from pu.core.interfaces.workflow import WorkflowEngine
from pu.db import connect
from pu.evaluation import (
    DefaultEvaluationHarness,
    DeterministicReportEvaluator,
    LLMReportJudge,
    ReportQualityPolicy,
)
from pu.harness import (
    CompositeToolAuditSink,
    DefaultSecurityHarness,
    DefaultToolHarness,
    InMemoryRateLimiter,
    JsonlToolAuditSink,
)
from pu.ops.agentops import AgentOpsHarness, SLOEvaluator
from pu.ops.tracing import Tracer
from pu.persistence import (
    SQLiteAgentEventRepository,
    SQLiteDatabase,
    SQLiteWorkflowRepository,
    build_operational_persistence,
)
from pu.providers.llm.catalog import resolve_provider_settings
from pu.providers.llm.factory import create_llm_provider
from pu.reporting import FileReportRepository, StructuredReportBuilder
from pu.runtime import DefaultAgentRuntime, Session
from pu.tools import build_registry
from pu.tools.registry import ToolRegistry


@dataclass
class ApplicationDependencies:
    settings: Settings
    conn: Any
    database: SQLiteDatabase
    llm_provider: LLMProvider
    memory: MemoryRepository
    tools: ToolRegistry
    session: Session
    tracer: Tracer
    agent_runtime: AgentRuntime
    workflow_engine: WorkflowEngine
    event_publisher: EventPublisher
    event_repository: SQLiteAgentEventRepository
    workflow_repository: SQLiteWorkflowRepository
    slo_evaluator: SLOEvaluator
    tool_harness: ToolHarness
    security_harness: SecurityHarness
    approval_service: HumanApprovalService
    approved_action_executor: ApprovedActionExecutor
    specialized_agents: dict[str, Any]
    agent_orchestrator: AgentOrchestrator
    report_builder: StructuredReportBuilder
    evaluation_harness: EvaluationHarness
    report_repository: FileReportRepository


def build_dependencies(
    settings: Settings | None = None,
    conn: Any = None,
    *,
    llm_provider: LLMProvider | None = None,
    check_same_thread: bool = True,
    agent_runtime: AgentRuntime | None = None,
    workflow_engine: WorkflowEngine | None = None,
    tool_harness: ToolHarness | None = None,
    security_harness: SecurityHarness | None = None,
) -> ApplicationDependencies:
    """Build one complete application object graph."""
    resolved_settings = settings or load_settings()
    resolved_settings.ensure_home()
    resolve_provider_settings(resolved_settings)
    resolved_conn = conn or connect(resolved_settings.home, check_same_thread=check_same_thread)
    operational = build_operational_persistence(resolved_settings.home)
    database = operational.database

    provider = llm_provider or create_llm_provider(resolved_settings)

    from pu.memory import Memory

    memory = Memory(resolved_conn, resolved_settings, provider)
    tools = build_registry(resolved_conn, resolved_settings, memory)
    session = Session(resolved_settings, memory=memory)
    tracer = Tracer(resolved_settings)
    event_repository = operational.events
    agentops = AgentOpsHarness(event_repository, tracer=tracer)
    slo_evaluator = SLOEvaluator(resolved_settings)
    workflow_repository = operational.workflows
    security = security_harness or DefaultSecurityHarness()
    approval_repository = operational.approvals
    approval_service = HumanApprovalService(
        approval_repository,
        ttl_minutes=resolved_settings.approval_ttl_minutes,
    )
    recovered_approvals = approval_repository.recover_inflight()
    for recovered in recovered_approvals:
        agentops.publish("approval_recovery_failed_closed", {
            "approval_id": recovered.id, "workflow_id": recovered.workflow_id,
            "request_id": recovered.request_id, "agent_id": recovered.agent_id,
            "execution_id": recovered.execution_id, "tool": recovered.tool_name,
            "status": recovered.status.value, "reason": recovered.error,
        })
    harness = tool_harness or DefaultToolHarness(
        tools,
        security,
        CompositeToolAuditSink(
            operational.tool_audit,
            JsonlToolAuditSink(resolved_settings.home / "traces" / "tool-audit.jsonl"),
        ),
        rate_limiter=InMemoryRateLimiter(
            limit=resolved_settings.tool_rate_limit_per_minute,
            window_seconds=60,
        ),
        approval_service=approval_service,
        read_retry_attempts=resolved_settings.tool_read_retries,
    )
    approved_action_executor = ApprovedActionExecutor(approval_service, harness)
    runtime = agent_runtime or DefaultAgentRuntime(provider, harness)
    workflow = workflow_engine or GraphWorkflowEngine()

    from pu.agents import (
        CalendarAgent,
        CompanyInfoAgent,
        EmailAgent,
        MemoryAgent,
        ReportGenerationAgent,
        WebResearchAgent,
    )
    from pu.agents.catalog import AGENT_TOOL_CATALOG
    from pu.orchestration import ResearchWorkflowOrchestrator

    def scoped(names):
        return tools.subset([name for name in names if tools.resolve(name) is not None])

    email_agent = EmailAgent(runtime, scoped(AGENT_TOOL_CATALOG["email"]), model=resolved_settings.model)
    company_agent = CompanyInfoAgent(runtime, scoped(AGENT_TOOL_CATALOG["company"]), model=resolved_settings.model)
    web_agent = WebResearchAgent(runtime, scoped(AGENT_TOOL_CATALOG["web"]), model=resolved_settings.model)
    calendar_agent = CalendarAgent(runtime, scoped(AGENT_TOOL_CATALOG["calendar"]), model=resolved_settings.model)
    memory_agent = MemoryAgent(runtime, scoped(AGENT_TOOL_CATALOG["memory"]), model=resolved_settings.model)
    report_agent = ReportGenerationAgent(runtime, scoped(AGENT_TOOL_CATALOG["report"]), model=resolved_settings.model, max_iterations=3, max_tokens=12000)
    specialized_agents = {
        "email": email_agent, "company": company_agent, "web": web_agent,
        "calendar": calendar_agent, "memory": memory_agent, "report": report_agent,
    }
    report_builder = StructuredReportBuilder()
    quality_policy = ReportQualityPolicy(
        min_overall=resolved_settings.report_quality_min_score,
        min_completeness=resolved_settings.report_min_completeness,
        min_source_coverage=resolved_settings.report_min_source_coverage,
        freshness_days=resolved_settings.report_freshness_days,
    )
    judge_model = resolved_settings.report_judge_model or resolved_settings.model
    report_judge = LLMReportJudge(provider, judge_model) if judge_model else None
    evaluation_harness = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(quality_policy),
        judge=report_judge,
    )
    report_repository = FileReportRepository(resolved_settings.home)
    orchestrator = ResearchWorkflowOrchestrator(
        email=email_agent, company=company_agent, web=web_agent,
        calendar=calendar_agent, memory=memory_agent, report=report_agent,
        report_builder=report_builder, evaluation_harness=evaluation_harness,
        report_repository=report_repository, workflow_repository=workflow_repository,
        run_judge=resolved_settings.report_judge_enabled,
    )

    return ApplicationDependencies(
        settings=resolved_settings,
        conn=resolved_conn,
        database=database,
        llm_provider=provider,
        memory=memory,
        tools=tools,
        session=session,
        tracer=tracer,
        agent_runtime=runtime,
        workflow_engine=workflow,
        event_publisher=agentops,
        event_repository=event_repository,
        workflow_repository=workflow_repository,
        slo_evaluator=slo_evaluator,
        tool_harness=harness,
        security_harness=security,
        approval_service=approval_service,
        approved_action_executor=approved_action_executor,
        specialized_agents=specialized_agents,
        agent_orchestrator=orchestrator,
        report_builder=report_builder,
        evaluation_harness=evaluation_harness,
        report_repository=report_repository,
    )


def create_application(
    settings: Settings | None = None,
    conn: Any = None,
    *,
    llm_provider: LLMProvider | None = None,
    check_same_thread: bool = True,
    agent_runtime: AgentRuntime | None = None,
    workflow_engine: WorkflowEngine | None = None,
    tool_harness: ToolHarness | None = None,
    security_harness: SecurityHarness | None = None,
):
    dependencies = build_dependencies(
        settings=settings,
        conn=conn,
        llm_provider=llm_provider,
        check_same_thread=check_same_thread,
        agent_runtime=agent_runtime,
        workflow_engine=workflow_engine,
        tool_harness=tool_harness,
        security_harness=security_harness,
    )
    from pu.app import PU

    return PU(dependencies=dependencies)
