"""Deterministic calendar free-slot derivation for research reports.

The Calendar Agent's read-only ``list_events`` tool returns plain text across
every connected calendar (Google first, local second). Rather than trusting
an LLM to eyeball availability, busy intervals are parsed and free working
windows computed deterministically; the result is attached to report.metadata
(JSON-safe dicts) so renderers and the chat tool can surface "lịch trống"
without any LLM involvement.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

# pu/tools/calendar.py:        "- Title: 2026-07-14T09:00 → 2026-07-14T10:00 with a@b"
# pu/tools/google_calendar.py: "Title | 2026-07-14T09:00:00+07:00 | a@b" (or date-only)
_LOCAL_RE = re.compile(
    r":\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})(?::\d{2})?\s*→\s*"
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})(?::\d{2})?"
)
_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WEEKDAYS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")


def _naive(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)  # compare in local wall time
    return dt


def parse_busy(text: str) -> list[tuple[datetime, datetime]]:
    """Extract busy intervals from ``list_events`` output text.

    Google's list output carries a start only (no end) — assume one hour,
    the common meeting length. A date-only entry is an all-day block.
    Prose lines ("No events found...", section headers) match nothing.
    """
    busy: list[tuple[datetime, datetime]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("-").strip()
        local = _LOCAL_RE.search(line)
        if local:
            start, end = _naive(local.group(1)), _naive(local.group(2))
            if start is not None and end is not None and end > start:
                busy.append((start, end))
            continue
        if "|" in line:
            fields = [f.strip() for f in line.split("|")]
            if len(fields) < 2:
                continue
            start = _naive(fields[1])
            if start is None:
                continue
            if _DATE_ONLY_RE.fullmatch(fields[1]):
                busy.append((
                    start.replace(hour=0, minute=0, second=0, microsecond=0),
                    start.replace(hour=23, minute=59, second=0, microsecond=0),
                ))
            else:
                busy.append((start, start + timedelta(hours=1)))
    return busy


def _round_up_half_hour(dt: datetime) -> datetime:
    stamp = dt.replace(second=0, microsecond=0)
    if stamp.minute % 30:
        stamp += timedelta(minutes=30 - stamp.minute % 30)
    elif stamp < dt:  # seconds were dropped → still belongs to the next bucket
        stamp += timedelta(minutes=30)
    return stamp


def derive_free_slots(
    busy: list[tuple[datetime, datetime]],
    *,
    reference: datetime | None = None,
    days: int = 7,
    day_start: int = 9,
    day_end: int = 18,
    min_minutes: int = 45,
) -> list[dict]:
    """Free working-hour windows for the next ``days`` days (Mon–Fri).

    Today's already-elapsed hours are not offered as free time.
    """
    reference = reference or datetime.now()
    result: list[dict] = []
    intervals = sorted((s, e) for s, e in busy if e > s)
    for offset in range(days):
        day = (reference + timedelta(days=offset)).date()
        if day.weekday() >= 5:  # Sat/Sun are not working days
            continue
        window_start = datetime.combine(day, time(day_start))
        window_end = datetime.combine(day, time(day_end))
        if offset == 0:
            window_start = max(window_start, _round_up_half_hour(reference))
        overlap = sorted(
            (max(s, window_start), min(e, window_end))
            for s, e in intervals
            if max(s, window_start) < min(e, window_end)
        )
        windows: list[tuple[datetime, datetime]] = []
        cursor = window_start
        for s, e in overlap:
            if s > cursor:
                windows.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < window_end:
            windows.append((cursor, window_end))
        windows = [(s, e) for s, e in windows if (e - s) >= timedelta(minutes=min_minutes)]
        if windows:
            result.append({
                "date": day.isoformat(),
                "label": f"{_WEEKDAYS[day.weekday()]} · {day.strftime('%d/%m')}",
                "windows": [f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}" for s, e in windows],
            })
    return result


def attach_free_slots(report, calendar_stage) -> bool:
    """Derive free slots from the calendar stage's ``list_events`` output and
    attach them to ``report.metadata``; re-renders markdown so the .md and
    chat-visible reply carry the same section. Returns False (no-op) when
    there is no calendar stage output to work with."""
    calls = getattr(calendar_stage, "tool_calls", None) or []
    outputs = [
        str(call.get("output") or "").strip()
        for call in calls
        if str(call.get("tool") or "") == "list_events"
    ]
    # No real list_events evidence → claiming free time would be over-claiming
    # (the same honesty rule list_events itself follows about WHICH calendars
    # were checked). An explicit "No events found..." output IS evidence.
    if not any(outputs):
        return False
    slots = derive_free_slots(parse_busy("\n".join(outputs)))
    if not slots:
        return False
    report.metadata["calendar_free_slots"] = slots[:5]
    from pu.reporting.evidence import StructuredReportBuilder
    report.markdown = StructuredReportBuilder.render_markdown(report)
    return True
