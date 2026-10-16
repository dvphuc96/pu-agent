"""Deterministic coverage for the chat-triggered research tool."""

from __future__ import annotations

from evals.deterministic.test_report_rendering import make_evaluation, make_report
from pu.tools.research import make_tool


class StubResult:
    def __init__(self, structured_report, evaluation=None, workflow_id="wf-42"):
        self.structured_report = structured_report
        self.evaluation = evaluation
        self.report = None
        self.workflow_id = workflow_id
        self.status = "completed"


def test_tool_metadata_schema_and_teaching_description(tmp_path):
    tool = make_tool(lambda prompt, observer=None: StubResult(None), tmp_path)
    assert tool.name == "research_partner"
    assert tool.capability == "research:run"
    assert tool.required_scope == "research:run"
    assert tool.side_effect is False
    assert tool.wants_notify is True
    schema = tool.to_api()
    assert schema["input_schema"]["required"] == ["company"]
    assert set(schema["input_schema"]["properties"]) == {
        "company", "website", "sender_email", "context",
    }
    description = tool.description.lower()
    assert "inbox" in description          # dùng sau khi đọc inbox
    assert "partner" in description or "customer" in description
    assert "approval" in description       # luôn đề nghị human duyệt
    # Must chain in the SAME turn: read_gmail_today's description ends with
    # "suggested next actions", which trains the model to stop and suggest —
    # this tool has to overpower that and make it act.
    assert "same turn" in description
    assert "do not stop" in description or "do not merely" in description
    # Input hygiene learned live: without a website the web agents fish noisy
    # lookalike domains (protonx.io vs protonx.co), and without timing the
    # report claims "no meeting details" though the email says "next week".
    props = schema["input_schema"]["properties"]
    assert "signature" in props["website"]["description"].lower()
    assert "dates" in props["context"]["description"].lower() or "time" in props["context"]["description"].lower()


def test_tool_builds_structured_prompt_and_forwards_observer(tmp_path):
    seen: dict = {}
    events: list = []

    def runner(prompt, observer=None):
        seen["prompt"] = prompt
        if observer is not None:
            observer("stage_started", {"stage": "email"})
        return StubResult(make_report(), make_evaluation())

    tool = make_tool(runner, tmp_path)
    out = tool.fn(
        company="ProtonX",
        website="https://protonx.co/",
        sender_email="huy@protonx.co",
        context="muốn hẹn buổi giới thiệu giải pháp",
        _notify=lambda kind, ev: events.append((kind, ev)),
    )
    prompt = seen["prompt"]
    assert "ProtonX" in prompt and "https://protonx.co/" in prompt
    assert "huy@protonx.co" in prompt and "giới thiệu giải pháp" in prompt
    assert events == [("stage_started", {"stage": "email"})]
    assert "Nghiên cứu" in out                       # Vietnamese report heading


def test_tool_output_summarizes_verdict_paths_and_workflow_id(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report-test-0001.pdf").write_bytes(b"%PDF-fake")
    tool = make_tool(lambda prompt, observer=None: StubResult(make_report(), make_evaluation()), tmp_path)
    out = tool.fn(company="ProtonX")
    assert "PASSED the quality gate (overall 81%)" in out
    assert str(reports / "report-test-0001.pdf") in out   # pdf preferred
    assert "wf-42" in out
    assert "approval" in out.lower()


def test_tool_output_falls_back_to_html_then_md(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report-test-0001.html").write_text("<html>ok</html>", encoding="utf-8")
    tool = make_tool(lambda prompt, observer=None: StubResult(make_report(), make_evaluation()), tmp_path)
    out = tool.fn(company="ProtonX")
    assert str(reports / "report-test-0001.html") in out
    assert ".pdf" not in out


def test_tool_failure_returns_error_guidance_without_raising(tmp_path):
    def boom(prompt, observer=None):
        raise RuntimeError("provider timeout")

    tool = make_tool(boom, tmp_path)
    out = tool.fn(company="ProtonX")
    assert "failed" in out.lower()
    assert "retry" in out.lower() or "resume" in out.lower()


def test_tool_output_includes_free_slots_when_available(tmp_path):
    report = make_report(metadata={"calendar_free_slots": [
        {"date": "2026-10-13", "label": "T3 · 13/10", "windows": ["09:00–11:30"]},
    ]})
    tool = make_tool(
        lambda prompt, observer=None: StubResult(report, make_evaluation()), tmp_path
    )
    out = tool.fn(company="ProtonX")
    assert "Lịch trống đề xuất" in out
    assert "T3 · 13/10" in out and "09:00–11:30" in out
    assert "timing" in out.lower()   # nudge: suggest a slot when discussing the meeting


def test_tool_output_carries_full_report_body_for_chat(tmp_path):
    """The chat IS the report: the tool returns the whole body (sections,
    gaps, sources) so the model can present it inline in Vietnamese instead
    of pointing the user at a file."""
    tool = make_tool(
        lambda prompt, observer=None: StubResult(make_report(), make_evaluation()), tmp_path
    )
    out = tool.fn(company="ProtonX")
    assert "White-label AI brokerage" in out            # section body, uncut
    assert "Courses <&> bootcamps" in out               # second section present
    assert "Tóm tắt" in out                             # Vietnamese headings
    assert "Thông tin còn thiếu" in out and "No revenue data" in out
    assert "Nguồn" in out and "S1" in out
    assert "Vietnamese" in out                          # relay instruction
    assert "archive" in out.lower()                     # file is just an archive copy


def test_application_registers_research_partner_tool(tmp_path):
    from evals.helpers import ScriptedProvider
    from pu.config import Settings
    from pu.core.container import create_application

    settings = Settings(home=tmp_path, api_key="offline", graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))

    tool = app.tools.resolve("research_partner")
    assert tool is not None
    assert tool.definition.required_scope == "research:run"
    assert not tool.definition.side_effect   # agent thấy trực tiếp, không qua propose_
    assert "research:run" in app.tools.scopes()
    assert any(d.name == "research_partner" for d in app.tools.agent_definitions())


def test_full_turn_sends_research_tool_and_system_directive(tmp_path):
    """End-to-end visibility: the live loop must actually ship the tool AND a
    system-prompt directive. Tool descriptions alone lose to read_gmail_today's
    "suggested next actions" template — the system prompt is the layer that
    makes the model chain read → research in one turn."""
    from evals.helpers import ScriptedProvider, response, text_block
    from pu.config import Settings
    from pu.core.container import create_application

    settings = Settings(home=tmp_path, api_key="offline", graph_workflows=False)
    client = ScriptedProvider([response([text_block("ok")]) for _ in range(4)])
    app = create_application(settings=settings, llm_provider=client)
    app.respond("hello")

    main = [r for r in client.requests if r.tools]
    assert main, "main loop request missing from provider calls"
    assert any(t.name == "research_partner" for t in main[0].tools)
    assert "research_partner" in main[0].system
    assert "same turn" in main[0].system
    assert "signature" in main[0].system        # pass the website from the signature
    assert "Vietnamese" in main[0].system       # reply in Vietnamese…
    assert "inline" in main[0].system           # …with the full report in the chat
