"""Bounded workspace research routes sources without five preprocessing calls."""

from __future__ import annotations

import json
from pathlib import Path

from pu.agents.calendar import CalendarAgent
from pu.agents.company_info import CompanyInfoAgent
from pu.agents.email import EmailAgent
from pu.agents.memory import MemoryAgent
from pu.agents.report import ReportGenerationAgent
from pu.agents.web_research import WebResearchAgent
from pu.core.interfaces.agents import AgentResult, AgentRunRequest
from pu.core.interfaces.orchestration import ResearchWorkflowRequest
from pu.orchestration.research import ResearchWorkflowOrchestrator
from pu.reporting.evidence import StructuredReportBuilder
from pu.reporting.workspace import WorkspaceRepository
from pu.tools.registry import ToolRegistry


class CountingRuntime:
    def __init__(self):
        self.calls: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest, observer=None) -> AgentResult:
        self.calls.append(request)
        assert request.tool_context.agent_id == "report-generation-agent"
        payload = {
            "schema_version": 2,
            "title": "Acme",
            "executive_summary": "Acme đã gửi yêu cầu hợp tác [W1].",
            "sections": {"company": "Acme [W1]", "email_need": "Yêu cầu hợp tác [W1]."},
            "claims": [
                {
                    "text": "Acme đã gửi yêu cầu hợp tác.",
                    "section": "email_need",
                    "source_ids": ["W1"],
                }
            ],
            "information_coverage": [],
            "missing_information": ["Cần xác minh phạm vi hợp tác."],
            "contradictions": [],
        }
        return AgentResult(
            reply=json.dumps(payload, ensure_ascii=False),
            iterations=1,
            usage={"in": 1, "out": 1, "calls": 1},
        )


class CaptureReportRepository:
    def __init__(self, root: Path):
        self.root = root / "reports"
        self.root.mkdir()
        self.saved = []

    def save(self, report, evaluation):
        self.saved.append((report, evaluation))


class PassingEvaluation:
    class Metrics:
        overall = 1.0
        unsupported_claims = 0

    def __init__(self):
        self.release_allowed = True
        self.gate_status = "pass"
        self.findings = []
        self.warnings = []
        self.judge = None
        self.status_details = {}
        self.evaluated = True
        self.metrics = self.Metrics()

    def to_dict(self):
        return {"release_allowed": True, "gate_status": "pass"}


class PassingHarness:
    def __init__(self):
        self.reports = []

    def evaluate(self, report, *, run_judge=False):
        self.reports.append(report)
        return PassingEvaluation()


def test_bounded_workspace_uses_role_contexts_and_only_report_llm(tmp_path):
    workspaces = WorkspaceRepository(tmp_path)
    workspace = workspaces.create("Acme", "https://acme.example", "alice@acme.example")
    sources = [
        workspaces.add_source(workspace["id"], title="Inbox", content="Acme asks to cooperate.", source_type="email"),
        workspaces.add_source(workspace["id"], title="CRM", content="Acme is a customer.", source_type="internal_knowledge"),
        workspaces.add_source(workspace["id"], title="Acme website", content="Acme products.", source_type="web", url="https://acme.example/about"),
        workspaces.add_source(workspace["id"], title="Calendar", content="Meeting next week.", source_type="calendar"),
        workspaces.add_source(workspace["id"], title="Memory", content="Prior Acme context.", source_type="memory"),
    ]
    runtime = CountingRuntime()
    tools = ToolRegistry()
    agents = {
        "email": EmailAgent(runtime, tools, model="fixture"),
        "company": CompanyInfoAgent(runtime, tools, model="fixture"),
        "web": WebResearchAgent(runtime, tools, model="fixture"),
        "calendar": CalendarAgent(runtime, tools, model="fixture"),
        "memory": MemoryAgent(runtime, tools, model="fixture"),
        "report": ReportGenerationAgent(runtime, tools, model="fixture"),
    }
    repository = CaptureReportRepository(tmp_path)
    harness = PassingHarness()
    orchestrator = ResearchWorkflowOrchestrator(
        email=agents["email"],
        company=agents["company"],
        web=agents["web"],
        calendar=agents["calendar"],
        memory=agents["memory"],
        report=agents["report"],
        report_builder=StructuredReportBuilder(),
        evaluation_harness=harness,
        report_repository=repository,
    )
    events = []
    result = orchestrator.run(
        ResearchWorkflowRequest(
            company="Acme",
            workspace_id=workspace["id"],
            evidence_sources=sources,
            partner_identity={"name": "Acme", "website": "https://acme.example"},
            evidence_cache={"reused": True, "checked_at": "2026-10-16T09:00:00+07:00"},
            require_semantic_review=True,
        ),
        observer=lambda kind, event: events.append((kind, event)),
    )

    assert len(runtime.calls) == 1
    assert runtime.calls[0].tool_context.agent_id == "report-generation-agent"
    prompt = runtime.calls[0].messages[0]["content"]
    assert "DETERMINISTIC ROLE ROUTING CONTEXT" in prompt
    for source in sources:
        assert source["id"] in prompt
    assert '"report_source_id":"W1"' in prompt
    assert set(result.stages) == {"email", "company", "web", "calendar", "memory"}
    assert all(stage.iterations == 0 and stage.usage["calls"] == 0 for stage in result.stages.values())
    started = {event[1]["stage"] for event in events if event[0] == "workflow_stage_started"}
    assert started == {"email", "company", "web", "calendar", "memory", "report"}
    assert result.evaluation.release_allowed is True
    assert result.structured_report.metadata["company_evidence_checked_at"] == "2026-10-16T09:00:00+07:00"
    assert result.structured_report.metadata["company_evidence_cache_reused"] is True
    assert result.structured_report.metadata["performance"]["evidence_cache_hit"] is True

