"""Phase 7 gates: specialized least-privilege agents + deterministic orchestrator."""
from __future__ import annotations

from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.orchestration import ResearchWorkflowRequest
from pu.db import connect
from pu.memory import Memory
from pu.tools import build_registry
from evals.helpers import ScriptedProvider


EXPECTED = {
    "email": {"read_email_inbox"},
    "company": {"search_knowledge", "search_memory"},
    "web": {"search_web", "read_web_page"},
    "calendar": {"list_events"},
    "memory": {"search_memory"},
    "report": set(),
}


class CapturingRuntime:
    def __init__(self):
        self.requests = []

    def run(self, request, observer=None):
        self.requests.append(request)
        aid = request.tool_context.agent_id
        return AgentResult(reply=f"output:{aid}", iterations=1, usage={"in": 1, "out": 1, "calls": 1})


def test_registry_has_phase7_read_paths_even_without_gmail_connector(tmp_path):
    settings = Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False)
    conn = connect(tmp_path)
    provider = ScriptedProvider([])
    memory = Memory(conn, settings, provider)
    registry = build_registry(conn, settings, memory)
    assert registry.resolve("read_email_inbox") is not None
    assert registry.resolve("read_email_inbox").definition.external is False
    assert registry.resolve("search_memory") is not None
    assert registry.resolve("search_memory").definition.capability == "memory:read"
    assert registry.resolve("search_memory").definition.side_effect is False
    assert registry.resolve("read_gmail_today") is None


def test_read_only_subset_does_not_get_action_propose(tmp_path):
    settings = Settings(home=tmp_path, api_key="offline", graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))
    subset = app.tools.subset(["search_web", "search_memory"])
    assert subset.agent_capabilities() == frozenset({"web:read", "memory:read"})
    assert "action:propose" not in subset.agent_capabilities()


def test_specialized_agents_have_exact_least_privilege_catalogs(tmp_path):
    settings = Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))
    assert set(app.specialized_agents) == set(EXPECTED)
    for key, expected_names in EXPECTED.items():
        agent = app.specialized_agents[key]
        names = {d.name for d in agent.tools.agent_definitions()}
        assert names == expected_names
        assert agent.descriptor.read_only is True
        assert "action:propose" not in agent.descriptor.capabilities
        assert not any(cap.endswith(":write") for cap in agent.descriptor.capabilities)


def test_gmail_enabled_adds_sync_read_only_only_to_email_agent(tmp_path):
    settings = Settings(home=tmp_path, api_key="offline", gmail=True, graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))
    email_names = {d.name for d in app.specialized_agents["email"].tools.agent_definitions()}
    assert email_names == {"read_email_inbox", "read_gmail_today"}
    assert all(not d.side_effect for d in app.specialized_agents["email"].tools.definitions())


def test_research_workflow_is_deterministic_and_fans_in_agent_outputs(tmp_path):
    runtime = CapturingRuntime()
    settings = Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]), agent_runtime=runtime)
    result = app.run_research_workflow("Research the best company context for today")
    assert result.status == "completed"
    assert list(result.stages) == ["email", "company", "web", "calendar", "memory"]
    assert result.report.reply == "output:report-generation-agent"
    assert [r.tool_context.agent_id for r in runtime.requests] == [
        "email-agent", "company-info-agent", "web-research-agent",
        "calendar-agent", "memory-agent", "report-generation-agent",
    ]
    # Every runtime request carries a scoped catalog and no write capability.
    for req in runtime.requests:
        assert not any(d.side_effect for d in req.tools.definitions())
        assert "action:propose" not in req.tool_context.capabilities
        assert not any(cap.endswith(":write") for cap in req.tool_context.capabilities)
    report_prompt = runtime.requests[-1].messages[0]["content"]
    for aid in ("email-agent", "company-info-agent", "web-research-agent", "calendar-agent", "memory-agent"):
        assert f"output:{aid}" in report_prompt


def test_orchestrator_descriptors_are_inspectable(tmp_path):
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    descriptors = app.agent_orchestrator.descriptors()
    assert [d["agent_id"] for d in descriptors] == [
        "email-agent", "company-info-agent", "web-research-agent",
        "calendar-agent", "memory-agent", "report-generation-agent",
    ]
    assert all(d["read_only"] for d in descriptors)


def test_email_and_memory_read_paths_execute_through_harness(tmp_path):
    from datetime import UTC, datetime
    from pu.core.interfaces.tools import ToolContext

    settings = Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))
    app.conn.execute(
        """INSERT INTO crm_emails (gmail_id, sender_name, sender_email, subject, snippet, body_text, internal_date, unread)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("m1", "Alice", "alice@example.com", "Acme renewal", "Need Q4 prep", "Need Q4 prep", datetime.now(UTC).isoformat(), 1),
    )
    app.conn.commit()
    app.memory.facts.add("acme", "Acme is an existing customer", source="user")

    email_ctx = ToolContext(actor="agent", principal="u", scopes=frozenset({"email:read"}), capabilities=frozenset({"email:read"}), agent_id="email-agent")
    email = app.tool_harness.execute("read_email_inbox", {"days": 7, "max_results": 5}, email_ctx)
    assert email.ok and "Acme renewal" in email.output

    memory_ctx = ToolContext(actor="agent", principal="u", scopes=frozenset({"memory:read"}), capabilities=frozenset({"memory:read"}), agent_id="memory-agent")
    mem = app.tool_harness.execute("search_memory", {"query": "Acme", "top_k": 5}, memory_ctx)
    assert mem.ok and "existing customer" in mem.output


def test_dashboard_exposes_phase7_agents_and_research_route():
    from pathlib import Path
    root = Path(__file__).parents[2]
    html = (root / "pu/ops/static/index.html").read_text(encoding="utf-8")
    js = (root / "pu/ops/static/js/views.js").read_text(encoding="utf-8")
    server = (root / "pu/ops/dashboard.py").read_text(encoding="utf-8")
    assert 'data-v="agents"' in html and 'data-v="research"' in html
    assert 'postJSON("/api/research"' in js
    assert '"/api/research": research_action' in server
