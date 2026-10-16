"""Phase 9: durable workflow recovery, migrations, audit and AgentOps SLOs."""
from __future__ import annotations

import json

import pytest

from evals.helpers import ScriptedProvider
from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.tools import ToolContext
from pu.db import connect
from pu.harness import DefaultSecurityHarness, DefaultToolHarness, InMemoryRateLimiter
from pu.ops.agentops import AgentOpsHarness, SLOEvaluator, prometheus_text
from pu.persistence import (
    SQLiteAgentEventRepository,
    SQLiteDatabase,
    SQLiteToolAuditSink,
    SQLiteWorkflowRepository,
)
from pu.tools.registry import Tool, ToolRegistry


def stage_result(reply: str, tool: str | None = None, output: str = "") -> AgentResult:
    calls = [] if not tool else [{"tool": tool, "args": {}, "output": output, "ok": True, "metadata": {}}]
    return AgentResult(reply=reply, tool_calls=calls, iterations=1, usage={"in": 1, "out": 1, "calls": 1})


class RecoverRuntime:
    def __init__(self, fail_agent: str | None = None):
        self.fail_agent = fail_agent
        self.calls: list[str] = []

    def run(self, request, observer=None):
        aid = request.tool_context.agent_id
        self.calls.append(aid)
        if aid == self.fail_agent:
            raise RuntimeError("simulated process interruption")
        if aid == "email-agent":
            return stage_result("Acme email", "read_email_inbox", "Alice <alice@acme.example> | Q4 planning")
        if aid == "company-info-agent":
            return stage_result("Acme customer", "search_knowledge", "Acme is an existing customer")
        if aid == "web-research-agent":
            return stage_result("Acme public", "search_web", "Acme launch https://acme.example/news")
        if aid == "calendar-agent":
            return stage_result("Meeting tomorrow", "list_events", "Acme review | 2026-10-05 09:00")
        if aid == "memory-agent":
            return stage_result("Prior renewal", "search_memory", "Acme renewal discussed last quarter")
        payload = {
            "title": "Acme meeting prep", "executive_summary": "Prepare for Acme Q4 planning.",
            "sections": {
                "company": "Existing customer", "contacts": "Alice", "email_need": "Q4 planning",
                "meeting_context": "Review meeting", "public_research": "Recent launch",
                "recent_updates": "Launch", "risks_opportunities": "Confirm renewal scope",
            },
            "claims": [
                {"text": "Alice requested Q4 planning", "section": "email_need", "source_ids": ["S1"]},
                {"text": "Acme is an existing customer", "section": "company", "source_ids": ["S3"]},
                {"text": "Acme has a public launch result", "section": "recent_updates", "source_ids": ["S5"]},
            ],
            "missing_information": ["Exact meeting objective"], "contradictions": [],
        }
        return AgentResult(reply=json.dumps(payload), iterations=1, usage={"in": 1, "out": 1, "calls": 1})


def test_migrations_are_versioned_idempotent_and_operational_tables_exist(tmp_path):
    conn = connect(tmp_path)
    first = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    assert any(row[0] == 9001 for row in first)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"workflow_runs", "workflow_stage_checkpoints", "agent_events", "tool_audit_events"} <= tables
    conn.close()
    conn2 = connect(tmp_path)
    second = conn2.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    assert list(first) == list(second)
    conn2.close()


def test_workflow_resumes_after_restart_without_rerunning_completed_stages(tmp_path):
    first_runtime = RecoverRuntime(fail_agent="web-research-agent")
    app1 = create_application(
        settings=Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False),
        llm_provider=ScriptedProvider([]), agent_runtime=first_runtime,
    )
    with pytest.raises(Exception) as err:
        app1.run_research_workflow("Prepare Acme")
    workflow_id = err.value.workflow_id
    run = app1.workflow_repository.get(workflow_id)
    assert run.status == "failed" and run.current_stage == "web"
    checkpoints = app1.workflow_repository.checkpoints(workflow_id)
    assert set(k for k,v in checkpoints.items() if v.status == "completed") == {"email", "company"}
    app1.close()

    second_runtime = RecoverRuntime()
    app2 = create_application(
        settings=Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False),
        llm_provider=ScriptedProvider([]), agent_runtime=second_runtime,
    )
    result = app2.resume_research_workflow(workflow_id)
    assert result.status == "completed"
    assert "email-agent" not in second_runtime.calls
    assert "company-info-agent" not in second_runtime.calls
    assert second_runtime.calls == ["web-research-agent", "calendar-agent", "memory-agent", "report-generation-agent"]
    final = app2.workflow_repository.get(workflow_id)
    assert final.status == "completed" and final.attempt == 2 and final.report_id
    app2.close()


def test_agentops_events_are_durable_correlated_and_project_to_slos(tmp_path):
    connect(tmp_path).close()
    db = SQLiteDatabase.from_home(tmp_path)
    repo = SQLiteAgentEventRepository(db)
    ops = AgentOpsHarness(repo)
    ops.publish("llm", {"workflow_id": "wf-1", "request_id": "req-1", "agent_id": "web-agent", "latency_ms": 120})
    ops.publish("tool_complete", {"workflow_id": "wf-1", "execution_id": "ex-1", "tool": "search_web", "ok": False, "latency_ms": 20})
    ops.publish("workflow_failed", {"workflow_id": "wf-1", "stage": "web", "error": "boom"})
    rows = repo.list(workflow_id="wf-1")
    assert len(rows) == 3
    assert {r["kind"] for r in rows} == {"llm", "tool_complete", "workflow_failed"}
    assert all(r["workflow_id"] == "wf-1" for r in rows)
    metrics = repo.aggregate()
    assert metrics["llm_p95_ms"] == 120
    assert metrics["tool_error_rate"] == 1.0
    assert metrics["workflow_success_rate"] == 0.0
    slo = SLOEvaluator(Settings(home=tmp_path, slo_tool_error_rate=0.1)).evaluate(metrics)
    assert slo["healthy"] is False
    text = prometheus_text(metrics, slo)
    assert "pu_tool_error_rate 1.0" in text and "pu_slo_healthy 0" in text


def test_tool_audit_is_queryable_sql_and_redacted(tmp_path):
    connect(tmp_path).close()
    db = SQLiteDatabase.from_home(tmp_path)
    audit = SQLiteToolAuditSink(db)
    registry = ToolRegistry()
    registry.register(Tool(
        name="lookup", description="external lookup",
        input_schema={"type":"object","properties":{"query":{"type":"string"},"api_key":{"type":"string"}},"required":["query","api_key"]},
        fn=lambda query, api_key: "ok", capability="web:read", required_scope="web:read", external=True,
    ))
    harness = DefaultToolHarness(
        registry, DefaultSecurityHarness(), audit,
        rate_limiter=InMemoryRateLimiter(limit=5, window_seconds=60),
    )
    ctx = ToolContext(
        actor="agent", principal="local-user", request_id="req", workflow_id="wf",
        agent_id="web", tool_call_id="call", scopes=frozenset({"web:read"}),
        capabilities=frozenset({"web:read"}),
    )
    # DLP blocks the secret before handler execution but the durable audit still exists and is redacted.
    result = harness.execute("lookup", {"query":"hello", "api_key":"sk-abcdefghijklmnop"}, ctx)
    assert result.ok is False
    rows = audit.list()
    assert rows and rows[0]["workflow_id"] == "wf"
    assert rows[0]["arguments"]["api_key"] == "[REDACTED]"


def test_phase9_composition_exposes_operational_repositories_and_slo_evaluator(tmp_path):
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    assert isinstance(app.database, SQLiteDatabase)
    assert isinstance(app.workflow_repository, SQLiteWorkflowRepository)
    assert isinstance(app.event_repository, SQLiteAgentEventRepository)
    assert app.slo_evaluator is not None
    app.close()


def test_external_read_retries_transient_error_but_write_never_auto_retries(tmp_path):
    connect(tmp_path).close()
    calls = {"read": 0, "write": 0}

    def flaky_read(query):
        calls["read"] += 1
        if calls["read"] == 1:
            raise TimeoutError("temporary")
        return "ok"

    def flaky_write(text):
        calls["write"] += 1
        raise TimeoutError("temporary")

    registry = ToolRegistry()
    registry.register(Tool(
        name="external_read", description="read",
        input_schema={"type":"object","properties":{"query":{"type":"string"}},"required":["query"]},
        fn=flaky_read, capability="web:read", required_scope="web:read", external=True,
    ))
    registry.register(Tool(
        name="external_write", description="write",
        input_schema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]},
        fn=flaky_write, capability="web:write", required_scope="web:write", external=True, side_effect=True,
    ))
    audit = SQLiteToolAuditSink(SQLiteDatabase.from_home(tmp_path))
    harness = DefaultToolHarness(
        registry, DefaultSecurityHarness(), audit, read_retry_attempts=2,
        rate_limiter=InMemoryRateLimiter(limit=10, window_seconds=60),
    )
    read_ctx = ToolContext(
        actor="agent", principal="local-user", scopes=frozenset({"web:read"}),
        capabilities=frozenset({"web:read"}), workflow_id="wf-retry",
    )
    assert harness.execute("external_read", {"query":"x"}, read_ctx).ok is True
    assert calls["read"] == 2
    assert any(r["status"] == "retrying" for r in audit.list())

    # Approved writes enter the concrete branch but are never automatically retried.
    from pu.harness.audit import action_hash
    args = {"text":"x"}
    write_ctx = ToolContext(
        actor="approved_action_executor", principal="local-user", scopes=frozenset({"web:write"}),
        capabilities=frozenset({"web:write"}), workflow_id="wf-retry",
        metadata={"approval_id":"a1", "approval_action_hash":action_hash("external_write", args), "approval_status":"executing"},
    )
    result = harness.execute("external_write", args, write_ctx)
    assert result.ok is False
    assert calls["write"] == 1


def test_restart_fails_closed_for_inflight_approved_action(tmp_path):
    from datetime import UTC, datetime, timedelta
    from pu.approval import ApprovalStatus, ProposedAction, SQLiteApprovalRepository

    conn = connect(tmp_path)
    repo = SQLiteApprovalRepository(SQLiteDatabase.from_home(tmp_path))
    now = datetime.now(UTC)
    action = ProposedAction(
        id="a-restart", tool_name="create_event", arguments={"title":"X","start":"2026-10-05T09:00:00"},
        action_hash="h", status=ApprovalStatus.APPROVED, principal="local-user", agent_id="calendar-agent",
        request_id="req", workflow_id="wf", tool_call_id="call", capability="calendar:write",
        required_scope="calendar:write", side_effect=True, external=True, risk="high",
        created_at=now.isoformat(), updated_at=now.isoformat(),
        expires_at=(now+timedelta(hours=1)).isoformat(), approved_action_hash="h",
    )
    repo.create(action)
    claimed = repo.claim_execution("a-restart", expected_hash="h", execution_id="ex-old")
    assert claimed.status is ApprovalStatus.EXECUTING
    conn.close()

    # New application startup must not auto-retry an uncertain side effect.
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    recovered = app.approval_service.get("a-restart")
    assert recovered.status is ApprovalStatus.FAILED
    assert "outcome may be uncertain" in (recovered.error or "")
    events = app.event_repository.list(limit=20)
    assert any(e["kind"] == "approval_recovery_failed_closed" and e["approval_id"] == "a-restart" for e in events)
    app.close()
