"""Deterministic calendar free-slot derivation for research reports."""
from __future__ import annotations

from datetime import datetime

from pu.core.interfaces.agents import AgentResult
from pu.reporting.calendar_slots import attach_free_slots, derive_free_slots, parse_busy
from pu.reporting.models import ReportSource, StructuredReport


def make_calendar_stage(outputs: list[str]) -> AgentResult:
    calls = [
        {"tool": "list_events", "args": {}, "output": out, "ok": True, "metadata": {}}
        for out in outputs
    ]
    return AgentResult(
        reply="calendar synthesis", tool_calls=calls,
        iterations=1, usage={"in": 1, "out": 1, "calls": 1},
    )


def make_report() -> StructuredReport:
    return StructuredReport(
        report_id="report-slots", workflow_id="wf-slots", title="Brief",
        executive_summary="Summary", sections={}, claims=[],
        sources=[ReportSource(
            id="S1", stage="calendar", source_type="calendar",
            title="list_events result", excerpt="",
        )],
        markdown="# Brief\n",
    )


def test_parse_busy_reads_local_arrow_and_google_pipe_formats():
    text = (
        "From Google Calendar:\n"
        "Weekly Sync | 2026-10-13T10:00:00+07:00 | boss@acme.example\n"
        "Out of office | 2026-10-15\n"
        "\nFrom pu's local calendar (events pu created):\n"
        "- Daily Standup: 2026-10-13T09:00 → 2026-10-13T09:30 with team\n"
        "No events found. Checked: pu's local calendar."
    )
    busy = parse_busy(text)
    spans = [(s.strftime("%m-%d %H:%M"), e.strftime("%m-%d %H:%M")) for s, e in busy]
    assert ("10-13 09:00", "10-13 09:30") in spans     # local arrow format
    assert ("10-13 10:00", "10-13 11:00") in spans     # google start-only → assume 1h
    assert ("10-15 00:00", "10-15 23:59") in spans     # google date-only → all-day
    assert len(busy) == 3                              # prose lines are not events


def test_derive_free_slots_subtracts_busy_windows_and_skips_weekends():
    reference = datetime(2026, 10, 12, 8, 0)           # Monday 08:00
    busy = [
        (datetime(2026, 10, 12, 9), datetime(2026, 10, 12, 10, 30)),
        (datetime(2026, 10, 12, 13), datetime(2026, 10, 12, 14)),
    ]
    slots = derive_free_slots(busy, reference=reference, days=7)
    monday = slots[0]
    assert monday["date"] == "2026-10-12"
    assert monday["label"].startswith("T2")
    assert monday["windows"] == ["10:30–13:00", "14:00–18:00"]
    tuesday = slots[1]
    assert tuesday["date"] == "2026-10-13"
    assert tuesday["windows"] == ["09:00–18:00"]
    dates = [item["date"] for item in slots]
    assert "2026-10-17" not in dates and "2026-10-18" not in dates  # T7/CN


def test_derive_free_slots_excludes_hours_already_past_today():
    reference = datetime(2026, 10, 12, 10, 10)        # Monday 10:10
    slots = derive_free_slots([], reference=reference, days=1)
    assert slots[0]["windows"] == ["10:30–18:00"]     # rounded up to the half hour


def test_derive_free_slots_filters_windows_below_minimum():
    reference = datetime(2026, 10, 12, 8, 0)
    busy = [(datetime(2026, 10, 12, 17, 45), datetime(2026, 10, 12, 18))]
    slots = derive_free_slots(busy, reference=reference, days=1)
    assert slots[0]["windows"] == ["09:00–17:45"]     # 15-min tail is dropped


def test_attach_free_slots_populates_metadata_and_markdown_section():
    stage = make_calendar_stage(["- Busy Block: 2026-10-13T09:00 → 2026-10-13T11:00"])
    report = make_report()
    assert attach_free_slots(report, stage) is True
    assert report.metadata["calendar_free_slots"]
    assert "Lịch trống" in report.markdown
    assert "## " in report.markdown                    # markdown fully re-rendered


def test_attach_free_slots_without_calendar_stage_is_a_noop():
    report = make_report()
    assert attach_free_slots(report, None) is False
    assert "calendar_free_slots" not in report.metadata
    assert report.markdown == "# Brief\n"


def test_research_workflow_attaches_free_slots_to_report(tmp_path):
    from evals.deterministic.test_phase8_report_evaluation import Phase8Runtime
    from evals.helpers import ScriptedProvider
    from pu.config import Settings
    from pu.core.container import create_application

    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False),
        llm_provider=ScriptedProvider([]), agent_runtime=Phase8Runtime(),
    )
    result = app.run_research_workflow("Prepare Acme")
    assert result.structured_report.metadata.get("calendar_free_slots")
    assert "Lịch trống" in result.report.reply
