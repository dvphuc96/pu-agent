"""Customer notebook isolation, grounding, availability and approved distribution."""

from __future__ import annotations

import base64
import io
import json
import socket
import sys
import types
import zipfile
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.llm import LLMResponse, LLMUsage
from pu.core.interfaces.tools import ToolContext, ToolResult
from pu.ops.workspace_api import ask_sources, workspace_action
from pu.reporting.attachments import extract_attachment
from pu.reporting.availability import free_windows, read_availability
from pu.reporting.workspace import WorkspaceRepository
from pu.tools.registry import Tool
from pu.tools.web_source import public_target


def report_payload(report_id="report-workspace-test", allowed=True):
    return {
        "report_id": report_id,
        "workflow_id": "workflow-test",
        "title": "Acme briefing",
        "executive_summary": "Acme cần tự động hóa.",
        "sections": {"company": "Acme"},
        "claims": [{"text": "Acme cần AI", "source_ids": ["S1"], "section": "company"}],
        "sources": [
            {
                "id": "S1",
                "title": "Email Acme",
                "source_type": "email",
                "excerpt": "Chúng tôi cần AI cho tài liệu.",
                "derived": False,
            }
        ],
        "markdown": "# Acme\n\nNhu cầu AI [S1].",
        "metadata": {},
        "evaluation": {"release_allowed": allowed, "gate_status": "pass" if allowed else "fail"},
    }


@pytest.fixture
def notebook(tmp_path):
    repo = WorkspaceRepository(tmp_path)
    workspace = repo.create("Acme", "https://acme.example", "alice@acme.example")
    return repo, workspace["id"]


class AnswerProvider:
    def __init__(self, answer=None):
        self.answer = answer or {"answer": "Khách hàng cần AI cho tài liệu.", "source_ids": ["S1"]}
        self.requests = []

    def complete(self, request, **kwargs):
        self.requests.append(request)
        return LLMResponse(text=json.dumps(self.answer), usage=LLMUsage(100, 30))


def fake_app(provider):
    return SimpleNamespace(
        llm_provider=provider,
        settings=SimpleNamespace(model="fake"),
        event_publisher=SimpleNamespace(publish=lambda *args: None),
    )


def test_sources_deduplicate_and_selection_survives_restart(notebook):
    repo, wid = notebook
    s = repo.add_source(wid, title="Email", content="Acme needs AI", source_type="email")
    assert repo.add_source(wid, title="Same email", content="Acme needs AI")["id"] == s["id"]
    repo.select_source(wid, s["id"], False)
    restored = WorkspaceRepository(repo.home)
    assert restored.selected(wid) == []
    assert restored.list()[0]["source_count"] == 1


def test_sources_and_reports_cannot_cross_workspaces(notebook):
    repo, wid = notebook
    other = repo.create("Other")["id"]
    s = repo.add_source(other, title="Private", content="Other company")
    with pytest.raises(ValueError):
        repo.selected(wid, [s["id"]])
    with pytest.raises(KeyError):
        repo.select_source(wid, s["id"], True)
    repo.attach_report(other, report_payload())
    with pytest.raises(KeyError):
        repo.require_report(wid, "report-workspace-test")


def test_report_versions_are_immutable_and_citations_have_source_map(notebook):
    repo, wid = notebook
    first = repo.attach_report(wid, report_payload())
    assert first["source_map"]["S1"] in {s["id"] for s in repo.sources(wid)}
    altered = report_payload()
    altered["markdown"] = "Changed"
    with pytest.raises(ValueError):
        repo.attach_report(wid, altered)
    repo.attach_report(wid, report_payload("report-v2"))
    assert len(repo.detail(wid)["versions"]) == 2
    assert repo.require_report(wid, "report-workspace-test")["payload"]["markdown"] != "Changed"


def test_failed_tool_evidence_is_not_imported(notebook):
    repo, wid = notebook
    data = report_payload()
    data["sources"][0]["metadata"] = {"tool_ok": False}
    repo.attach_report(wid, data)
    assert repo.sources(wid) == []


def test_ask_only_selected_sources_has_no_tools_and_persists_citations(notebook):
    repo, wid = notebook
    included = repo.add_source(wid, title="Email", content="Acme needs AI")
    excluded = repo.add_source(wid, title="Hidden", content="UNSELECTED_SECRET_CONTEXT")
    repo.select_source(wid, excluded["id"], False)
    repo.add_message(wid, "Old question", "UNSELECTED_SECRET_CONTEXT", [excluded["id"]])
    provider = AnswerProvider()
    result = ask_sources(repo, wid, "Nhu cầu?", repo.selected(wid), fake_app(provider))
    assert result["source_ids"] == [included["id"]]
    assert provider.requests[0].tools == []
    assert "UNSELECTED_SECRET_CONTEXT" not in provider.requests[0].messages[0]["content"]
    assert repo.detail(wid)["messages"][-1]["source_ids"] == [included["id"]]


def test_ask_rejects_invented_citation_and_does_not_save_answer(notebook):
    repo, wid = notebook
    repo.add_source(wid, title="Email", content="Acme")
    with pytest.raises(ValueError):
        ask_sources(
            repo,
            wid,
            "Nhu cầu?",
            repo.selected(wid),
            fake_app(AnswerProvider({"answer": "Invented", "source_ids": ["S999"]})),
        )
    assert repo.detail(wid)["messages"] == []


def test_uncited_answer_fails_closed(notebook):
    repo, wid = notebook
    repo.add_source(wid, title="Email", content="Acme")
    result = ask_sources(
        repo,
        wid,
        "Revenue?",
        repo.selected(wid),
        fake_app(AnswerProvider({"answer": "Invented revenue", "source_ids": []})),
    )
    assert "Chưa đủ bằng chứng" in result["answer"]


def test_empty_selection_never_calls_model(notebook):
    repo, wid = notebook
    provider = AnswerProvider()
    with pytest.raises(ValueError):
        ask_sources(repo, wid, "Question", [], fake_app(provider))
    assert provider.requests == []


def test_availability_uses_real_end_and_converts_timezone():
    start = datetime(2026, 10, 14, 8, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    events = [{"start": "2026-10-14T02:00:00Z", "end": "2026-10-14T05:00:00Z"}]
    slots = free_windows(events, start=start, days=1, duration=45)
    assert slots[0]["window"] == "12:00–12:45"
    assert slots[0]["free_until"].endswith("18:00:00+07:00")


def test_all_day_and_overlapping_events_block_entire_interval():
    start = datetime(2026, 10, 14, 8, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert (
        free_windows(
            [{"start": "2026-10-14", "end": "2026-10-15"}], start=start, days=1, duration=30
        )
        == []
    )
    slots = free_windows(
        [
            {"start": "2026-10-14T09:00", "end": "2026-10-14T12:00"},
            {"start": "2026-10-14T11:00", "end": "2026-10-14T14:00"},
        ],
        start=start,
        days=1,
        duration=30,
    )
    assert slots[0]["window"] == "14:00–14:30"


def test_google_failure_does_not_claim_calendar_is_free(notebook, monkeypatch):
    repo, _wid = notebook
    (repo.home / "google-token.json").write_text("{}")
    import pu.reporting.availability as module

    monkeypatch.setattr(
        module, "read_google_events", lambda *args: (_ for _ in ()).throw(ValueError("offline"))
    )
    result = read_availability(
        repo.home, reference=datetime(2026, 10, 14, 8, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    )
    assert result["status"] == "unknown" and result["slots"] == []


def test_local_only_availability_labels_its_scope(notebook):
    repo, _wid = notebook
    result = read_availability(
        repo.home, reference=datetime(2026, 10, 14, 8, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    )
    assert result["checked_sources"] == ["Lịch local"]
    assert "Chỉ kiểm tra lịch local" in result["warnings"][0]


def test_structured_tool_failure_survives_wrapper():
    tool = Tool("failed", "", {}, lambda: ToolResult("Failed", ok=False, error="offline"))
    assert tool.execute({}, ToolContext()).ok is False


def test_knowledge_delivery_requires_approval_and_is_idempotent(notebook):
    repo, wid = notebook
    stored = repo.attach_report(wid, report_payload())
    app = create_application(
        Settings(home=repo.home, api_key="offline"), llm_provider=AnswerProvider()
    )
    try:
        args = {
            "workspace_id": wid,
            "report_id": "report-workspace-test",
            "content_hash": stored["content_hash"],
        }
        context = ToolContext(
            principal="local-user",
            scopes=frozenset({"knowledge:write"}),
            capabilities=frozenset({"action:propose"}),
        )
        proposal = app.tool_harness.execute("propose_save_report_to_knowledge", args, context)
        with repo.database.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0] == 0
        aid = proposal.metadata["approval_id"]
        app.approval_service.approve(aid)
        _, result = app.approved_action_executor.execute(aid)
        assert result.ok
        with pytest.raises(ValueError):
            app.approved_action_executor.execute(aid)
        second = app.tool_harness.execute("propose_save_report_to_knowledge", args, context)
        app.approval_service.approve(second.metadata["approval_id"])
        assert app.approved_action_executor.execute(second.metadata["approval_id"])[1].ok
        with repo.database.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0] == 1
    finally:
        app.close()


def test_distribution_rejects_wrong_hash_or_failed_quality_gate(notebook):
    repo, wid = notebook
    stored = repo.attach_report(wid, report_payload(allowed=False))
    with pytest.raises(ValueError):
        repo.require_report(wid, "report-workspace-test", "changed")
    app = create_application(
        Settings(home=repo.home, api_key="offline"), llm_provider=AnswerProvider()
    )
    try:
        with pytest.raises(ValueError, match="Quality gate"):
            workspace_action(
                repo.home,
                {
                    "op": "propose",
                    "workspace_id": wid,
                    "report_id": "report-workspace-test",
                    "content_hash": stored["content_hash"],
                    "tool": "save_report_to_knowledge",
                },
                app_factory=lambda: app,
            )
    finally:
        app.close()


def test_docx_extracts_text_and_blocks_unsupported_files():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Acme AI</w:t></w:r></w:p></w:body></w:document>',
        )
    result = extract_attachment("company.docx", base64.b64encode(stream.getvalue()).decode())
    assert result["content"] == "Acme AI"
    with pytest.raises(ValueError):
        extract_attachment("unsafe.exe", base64.b64encode(b"hello").decode())


def test_web_fetch_rejects_private_and_mixed_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError):
        public_target("https://example.com")
    with pytest.raises(ValueError):
        public_target("https://user:password@example.com")


def test_gmail_uncertain_send_is_not_repeated(notebook, monkeypatch):
    repo, wid = notebook
    stored = repo.attach_report(wid, report_payload())
    import pu.tools.report_delivery as delivery

    monkeypatch.setattr(delivery, "send_credentials", lambda home: object())
    calls = []

    def execute(**kwargs):
        calls.append(1)
        raise TimeoutError("ambiguous")

    service = SimpleNamespace(
        users=lambda: SimpleNamespace(
            messages=lambda: SimpleNamespace(send=lambda **kwargs: SimpleNamespace(execute=execute))
        )
    )
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *args, **kwargs: service
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    tool = delivery.make_tools(repo.home)[1]
    args = {
        "workspace_id": wid,
        "report_id": "report-workspace-test",
        "content_hash": stored["content_hash"],
        "recipient": "alice@acme.example",
        "subject": "Research",
    }
    ctx = ToolContext(metadata={"approval_action_hash": "test-hash"})
    assert tool.execute(args, ctx).ok is False
    assert tool.execute(args, ctx).ok is False
    assert calls == [1]


def test_report_secret_is_blocked_before_loading_gmail_credentials(notebook, monkeypatch):
    import pu.tools.report_delivery as delivery

    repo, wid = notebook
    payload = report_payload()
    payload["markdown"] = "Secret: sk-abcdefghijklmnop"
    stored = repo.attach_report(wid, payload)
    monkeypatch.setattr(
        delivery, "send_credentials", lambda *args: pytest.fail("Must not load credentials")
    )
    result = delivery.make_tools(repo.home)[1].execute(
        {
            "workspace_id": wid,
            "report_id": payload["report_id"],
            "content_hash": stored["content_hash"],
            "recipient": "alice@acme.example",
            "subject": "Report",
        },
        ToolContext(metadata={"approval_action_hash": "fake"}),
    )
    assert result.ok is False and result.error == "secret_egress"


def test_research_keeps_customer_target_and_imports_selected_evidence(notebook):
    from pu.core.interfaces.agents import AgentResult
    from pu.core.interfaces.orchestration import ResearchWorkflowRequest
    from pu.evaluation.report import DefaultEvaluationHarness
    from pu.orchestration.research import ResearchWorkflowOrchestrator
    from pu.reporting.evidence import StructuredReportBuilder
    from pu.reporting.repository import FileReportRepository

    repo, wid = notebook
    source = repo.add_source(
        wid, title="Customer email", content="Acme needs AI", source_type="email"
    )
    prompts = []

    class Agent:
        descriptor = SimpleNamespace(agent_id="fake")

        def respond(self, prompt, **kwargs):
            prompts.append(prompt)
            return AgentResult(reply="Selected customer: Acme")

    class ReportAgent(Agent):
        def respond(self, prompt, **kwargs):
            return AgentResult(
                reply=json.dumps(
                    {
                        "title": "Acme",
                        "executive_summary": "AI request",
                        "sections": {
                            key: "AI request"
                            for key in [
                                "company",
                                "contacts",
                                "email_need",
                                "meeting_context",
                                "public_research",
                                "risks_opportunities",
                            ]
                        },
                        "claims": [
                            {"text": "Acme needs AI", "source_ids": ["W1"], "section": "company"}
                        ],
                    }
                )
            )

    agent = Agent()
    orchestration = ResearchWorkflowOrchestrator(
        email=agent,
        company=agent,
        web=agent,
        calendar=agent,
        memory=agent,
        report=ReportAgent(),
        report_builder=StructuredReportBuilder(),
        evaluation_harness=DefaultEvaluationHarness(),
        report_repository=FileReportRepository(repo.home),
    )
    result = orchestration.run(
        ResearchWorkflowRequest(workspace_id=wid, company="Acme", evidence_sources=[source])
    )
    assert all("Acme" in prompt and "Do not replace the target" in prompt for prompt in prompts)
    assert result.structured_report.metadata["workspace_id"] == wid
    stored = repo.require_report(wid, result.structured_report.report_id)
    assert stored["source_map"]["W1"] == source["id"]


def test_docx_export_roundtrips_vietnamese_and_citations():
    from pu.reporting.docx import render_docx

    payload = report_payload()
    payload["title"] = "Nghiên cứu khách hàng"
    data = render_docx(payload)
    extracted = extract_attachment("report.docx", base64.b64encode(data).decode())
    assert "Nghiên cứu khách hàng" in extracted["content"]
    assert "[S1]" in extracted["content"]


def test_export_renderer_accepts_persisted_evaluation_dict():
    from pu.reporting.render import ReportRenderer

    html = ReportRenderer._verdict_html(
        {"release_allowed": True, "gate_status": "pass", "metrics": {"overall": 0.9}}
    )
    assert "90%" in html and "CHƯA ĐẠT" not in html


def test_revision_with_invented_or_missing_citation_fails_quality_gate():
    from pu.evaluation.report import DeterministicReportEvaluator
    from pu.reporting.revisions import revise_report

    revised = revise_report(report_payload(), {"sections": {"company": "Invented revenue [S999]"}})
    result = DeterministicReportEvaluator().evaluate(revised)
    assert result.release_allowed is False
    assert result.metrics.invalid_source_refs > 0
    assert revised.report_id != "report-workspace-test"


def test_report_edit_creates_new_version_and_cancels_old_pending_proposal(notebook):
    repo, wid = notebook
    stored = repo.attach_report(wid, report_payload())
    app = create_application(
        Settings(home=repo.home, api_key="offline"), llm_provider=AnswerProvider()
    )
    try:
        proposal = workspace_action(
            repo.home,
            {
                "op": "propose",
                "workspace_id": wid,
                "report_id": "report-workspace-test",
                "content_hash": stored["content_hash"],
                "tool": "save_report_to_knowledge",
            },
            app_factory=lambda: app,
        )
        revised = workspace_action(
            repo.home,
            {
                "op": "revise",
                "workspace_id": wid,
                "report_id": "report-workspace-test",
                "content_hash": stored["content_hash"],
                "changes": {"title": "Updated briefing"},
            },
            app_factory=lambda: app,
        )
        assert revised["report"]["payload"]["title"] == "Updated briefing"
        assert len(revised["versions"]) == 2
        assert app.approval_service.get(proposal["approval_id"]).status.value == "rejected"
        assert (
            repo.require_report(wid, "report-workspace-test")["payload"]["title"] == "Acme briefing"
        )
    finally:
        app.close()
