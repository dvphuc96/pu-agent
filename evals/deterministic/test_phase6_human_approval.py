"""Phase 6 gates: durable Human Approval and one-shot write capability release."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evals.helpers import ScriptedProvider
from pu.approval import (
    ApprovalStatus,
    ApprovedActionExecutor,
    HumanApprovalService,
    SQLiteApprovalRepository,
)
from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.security import SecurityDecision
from pu.core.interfaces.tools import ToolContext
from pu.db import connect
from pu.persistence import SQLiteDatabase
from pu.harness import DefaultSecurityHarness, DefaultToolHarness, InMemoryToolAuditSink
from pu.tools.registry import Tool, ToolRegistry


def write_tool(calls, *, external=False):
    return Tool(
        name="write_x",
        description="write x",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        fn=lambda text: (calls.append(text), f"wrote:{text}")[1],
        capability="x:write",
        required_scope="x:write",
        side_effect=True,
        external=external,
    )


def setup_approval(tmp_path, *, security=None, tool=None):
    conn = connect(tmp_path)
    repo = SQLiteApprovalRepository(SQLiteDatabase.from_home(tmp_path))
    service = HumanApprovalService(repo, ttl_minutes=60)
    registry = ToolRegistry()
    calls = []
    registry.register(tool or write_tool(calls))
    audit = InMemoryToolAuditSink()
    harness = DefaultToolHarness(
        registry,
        security or DefaultSecurityHarness(),
        audit,
        approval_service=service,
    )
    executor = ApprovedActionExecutor(service, harness)
    return conn, repo, service, registry, harness, executor, audit, calls


def agent_context(registry, **metadata):
    return ToolContext(
        actor="agent",
        principal="local-user",
        request_id="req-6",
        workflow_id="wf-6",
        agent_id="research-agent",
        tool_call_id="call-6",
        scopes=registry.scopes(),
        capabilities=registry.agent_capabilities(),
        metadata=metadata,
    )


def test_agent_catalog_exposes_propose_alias_and_never_write_capability():
    calls = []
    registry = ToolRegistry()
    registry.register(write_tool(calls))
    registry.register(Tool(
        name="read_x", description="read", input_schema={"type":"object","properties":{}},
        fn=lambda: "read", capability="x:read", required_scope="x:read",
    ))
    defs = {item.name: item for item in registry.agent_definitions()}
    assert "propose_write_x" in defs
    assert defs["propose_write_x"].capability == "action:propose"
    assert "write_x" not in defs
    assert "read_x" in defs
    assert "x:write" not in registry.agent_capabilities()
    assert registry.agent_capabilities() == frozenset({"x:read", "action:propose"})


def test_agent_write_call_creates_proposal_without_executing(tmp_path):
    _, _, service, registry, harness, _, audit, calls = setup_approval(tmp_path)
    result = harness.execute("propose_write_x", {"text":"hello"}, agent_context(registry))
    assert result.ok
    assert result.metadata["approval_status"] == "pending"
    assert result.metadata["action_tool"] == "write_x"
    assert calls == []
    action = service.get(result.metadata["approval_id"])
    assert action is not None and action.status is ApprovalStatus.PENDING
    assert action.arguments == {"text":"hello"}
    assert action.capability == "x:write"
    assert [r.status for r in audit.records] == ["attempted", "approval_pending"]


def test_stale_direct_write_name_is_still_proposal_only(tmp_path):
    _, _, service, registry, harness, _, _, calls = setup_approval(tmp_path)
    result = harness.execute("write_x", {"text":"legacy"}, agent_context(registry))
    assert result.ok and result.metadata["approval_id"]
    assert service.get(result.metadata["approval_id"]).tool_name == "write_x"
    assert calls == []


def test_agent_cannot_execute_write_even_if_it_is_given_write_capability(tmp_path):
    _, _, _, registry, harness, _, _, calls = setup_approval(tmp_path)
    ctx = ToolContext(
        actor="agent", principal="u", scopes=frozenset({"x:write"}),
        capabilities=frozenset({"x:write"}),
    )
    result = harness.execute("write_x", {"text":"nope"}, ctx)
    assert not result.ok and result.error == "capability_denied"
    assert calls == []


def test_edit_changes_hash_revision_and_requires_reapproval(tmp_path):
    _, _, service, registry, harness, _, _, calls = setup_approval(tmp_path)
    proposed = harness.execute("propose_write_x", {"text":"one"}, agent_context(registry))
    action = service.approve(proposed.metadata["approval_id"], decided_by="human")
    old_hash = action.action_hash
    edited = service.edit(action.id, {"text":"two"})
    assert edited.status is ApprovalStatus.PENDING
    assert edited.revision == 2
    assert edited.action_hash != old_hash
    assert edited.approved_action_hash is None
    assert calls == []


def test_approve_executes_exact_payload_once_and_records_human_actor(tmp_path):
    _, _, service, registry, harness, executor, _, calls = setup_approval(tmp_path)
    proposed = harness.execute("propose_write_x", {"text":"once"}, agent_context(registry))
    approved = service.approve(
        proposed.metadata["approval_id"], note="looks right", decided_by="local-dashboard-user"
    )
    assert approved.approved_action_hash == approved.action_hash
    assert approved.decided_by == "local-dashboard-user"
    final, result = executor.execute(approved.id)
    assert result.ok and result.output == "wrote:once"
    assert final.status is ApprovalStatus.EXECUTED
    assert calls == ["once"]
    with pytest.raises(ValueError, match="not approved"):
        executor.execute(approved.id)
    assert calls == ["once"]


def test_reject_never_releases_write_capability(tmp_path):
    _, _, service, registry, harness, executor, _, calls = setup_approval(tmp_path)
    proposed = harness.execute("propose_write_x", {"text":"reject me"}, agent_context(registry))
    rejected = service.reject(proposed.metadata["approval_id"], decided_by="human")
    assert rejected.status is ApprovalStatus.REJECTED
    with pytest.raises(ValueError, match="not approved"):
        executor.execute(rejected.id)
    assert calls == []


def test_expired_proposal_cannot_be_approved(tmp_path):
    conn, _, service, registry, harness, _, _, _ = setup_approval(tmp_path)
    proposed = harness.execute("propose_write_x", {"text":"late"}, agent_context(registry))
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="milliseconds")
    conn.execute("UPDATE proposed_actions SET expires_at=? WHERE id=?", (past, proposed.metadata["approval_id"]))
    conn.commit()
    action = service.get(proposed.metadata["approval_id"])
    assert action.status is ApprovalStatus.EXPIRED
    with pytest.raises(ValueError, match="expired"):
        service.approve(action.id, decided_by="human")


def test_execute_time_security_is_rechecked_after_human_approval(tmp_path):
    class DenyAtExecution:
        name = "execution_recheck"
        def evaluate(self, tool, arguments, context):
            if context.metadata.get("proposal_only"):
                return SecurityDecision(True, "proposal may be reviewed", self.name)
            return SecurityDecision(False, "execution blocked after approval", self.name)

    calls = []
    security = DefaultSecurityHarness([DenyAtExecution()])
    _, _, service, registry, harness, executor, audit, _ = setup_approval(
        tmp_path, security=security, tool=write_tool(calls, external=True)
    )
    proposed = harness.execute("propose_write_x", {"text":"review me"}, agent_context(registry))
    assert proposed.ok
    approved = service.approve(proposed.metadata["approval_id"], decided_by="human")
    final, result = executor.execute(approved.id)
    assert not result.ok and result.error == "security_denied"
    assert final.status is ApprovalStatus.FAILED
    assert calls == []
    assert any(r.status == "blocked" and r.policy == "execution_recheck" for r in audit.records)


def test_approval_binding_rejects_hash_mismatch(tmp_path):
    _, _, _, registry, harness, _, _, calls = setup_approval(tmp_path)
    ctx = ToolContext(
        actor="approved_action_executor", principal="u",
        scopes=frozenset({"x:write"}), capabilities=frozenset({"x:write"}),
        metadata={
            "approval_id":"a1", "approval_action_hash":"wrong",
            "approval_status":"executing",
        },
    )
    result = harness.execute("write_x", {"text":"tampered"}, ctx)
    assert not result.ok and result.error == "approval_denied"
    assert result.metadata["policy"] == "approval_binding"
    assert calls == []


def test_product_composition_wires_durable_approval_and_agent_has_no_writes(tmp_path):
    settings = Settings(home=tmp_path, api_key="offline", graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))
    assert isinstance(app.approval_service, HumanApprovalService)
    assert isinstance(app.approved_action_executor, ApprovedActionExecutor)
    write_caps = {d.capability for d in app.tools.definitions() if d.side_effect}
    assert write_caps
    assert write_caps.isdisjoint(app.tools.agent_capabilities())
    assert "action:propose" in app.tools.agent_capabilities()
    assert app.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='proposed_actions'"
    ).fetchone() is not None


def test_no_runtime_code_grants_all_registry_capabilities_to_agent_context():
    root = Path(__file__).parents[2] / "pu"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "capabilities=self.tools.capabilities()" in text or "capabilities=request.tools.capabilities()" in text:
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []
