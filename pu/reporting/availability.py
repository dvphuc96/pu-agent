"""Availability from complete structured intervals, never inferred from prose."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pu.persistence.database import SQLiteDatabase
from pu.reporting.policy import AVAILABILITY_MAX_AGE_SECONDS


def snapshot_is_current(snapshot: dict, *, reference: datetime | None = None) -> bool:
    """A report snapshot is an observation, never a reservation of a free slot."""
    try:
        stamp = datetime.fromisoformat(snapshot["checked_at"])
        if stamp.tzinfo is None:
            return False
        current = reference or datetime.now(UTC)
        age = (current - stamp).total_seconds()
        if not 0 <= age <= AVAILABILITY_MAX_AGE_SECONDS:
            return False
        for slot in snapshot.get("slots", []):
            start = datetime.fromisoformat(slot["start"])
            if start.tzinfo is None or start <= current:
                return False
        return True
    except (KeyError, ValueError, TypeError):
        return False


def week_options(*, reference: datetime | None = None) -> dict:
    current = (reference or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    return {"reference": current, "days": 7 - current.weekday(), "timezone": "Asia/Ho_Chi_Minh"}


def free_windows(events: list[dict], *, start: datetime, days: int, duration: int) -> list[dict]:
    busy = []
    for event in events:
        if event.get("cancelled") or event.get("transparent"):
            continue
        lo = datetime.fromisoformat(event["start"])
        hi = datetime.fromisoformat(event["end"])
        lo = lo.replace(tzinfo=start.tzinfo) if lo.tzinfo is None else lo.astimezone(start.tzinfo)
        hi = hi.replace(tzinfo=start.tzinfo) if hi.tzinfo is None else hi.astimezone(start.tzinfo)
        if hi <= lo:
            raise ValueError("Lịch có khoảng thời gian không hợp lệ.")
        busy.append((lo, hi))
    slots = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        if day.weekday() >= 5:
            continue
        lo = max(datetime.combine(day, time(9), tzinfo=start.tzinfo), start)
        lo = lo.replace(second=0, microsecond=0) + (
            timedelta(minutes=1) if lo.second or lo.microsecond else timedelta()
        )
        if lo.minute % 30:
            lo += timedelta(minutes=30 - lo.minute % 30)
        end = datetime.combine(day, time(18), tzinfo=start.tzinfo)
        intervals = sorted((max(s, lo), min(e, end)) for s, e in busy if max(s, lo) < min(e, end))
        cursor = lo
        windows = []
        for s, e in intervals:
            if s - cursor >= timedelta(minutes=duration):
                windows.append((cursor, s))
            cursor = max(cursor, e)
        if end - cursor >= timedelta(minutes=duration):
            windows.append((cursor, end))
        for s, e in windows:
            candidate_end = s + timedelta(minutes=duration)
            slots.append(
                {
                    "start": s.isoformat(),
                    "end": candidate_end.isoformat(),
                    "free_until": e.isoformat(),
                    "date": day.isoformat(),
                    "label": day.strftime("%d/%m"),
                    "window": f"{s:%H:%M}–{candidate_end:%H:%M}",
                    "duration_minutes": duration,
                }
            )
    return slots


def read_availability(
    home,
    *,
    start_date: str = "",
    days: int = 7,
    duration: int = 45,
    timezone: str = "Asia/Ho_Chi_Minh",
    reference: datetime | None = None,
) -> dict:
    if not 1 <= days <= 30 or not 15 <= duration <= 480:
        raise ValueError("Khoảng xem lịch 1–30 ngày, thời lượng 15–480 phút.")
    zone = ZoneInfo(timezone)
    current = (reference or datetime.now(UTC)).astimezone(zone)
    start = (
        datetime.combine(datetime.fromisoformat(start_date).date(), time(), tzinfo=zone)
        if start_date
        else current
    )
    start = max(start, current)
    end = datetime.combine((start + timedelta(days=days)).date(), time(), tzinfo=zone)
    database = SQLiteDatabase.from_home(home)
    with database.connection() as conn:
        rows = conn.execute('SELECT id,title,start,"end",attendees FROM calendar_events').fetchall()
    events = [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "start": r["start"],
            "end": r["end"],
            "attendees": r["attendees"],
            "source": "Lịch local",
        }
        for r in rows
    ]
    checked = ["Lịch local"]
    warnings = []
    complete = True
    from pu.tools.google_calendar import is_connected

    if is_connected(home):
        try:
            events.extend(read_google_events(home, start, end))
            checked.append("Google Calendar (primary)")
        except Exception:
            complete = False
            warnings.append(
                "Chưa đọc được Google Calendar. Không thể xác nhận lịch trống; hãy kết nối lại rồi kiểm tra."
            )
    else:
        warnings.append("Chỉ kiểm tra lịch local. Kết nối Google Calendar để kiểm tra lịch Google.")
    try:
        slots = free_windows(events, start=start, days=days, duration=duration) if complete else []
    except (ValueError, KeyError, TypeError):
        slots, complete = [], False
        warnings.append("Một sự kiện thiếu start/end hợp lệ. Chưa thể xác nhận lịch trống.")
    relevant = []
    for ev in events:
        try:
            lo = datetime.fromisoformat(ev["start"])
            hi = datetime.fromisoformat(ev["end"])
            lo = lo.replace(tzinfo=zone) if lo.tzinfo is None else lo.astimezone(zone)
            hi = hi.replace(tzinfo=zone) if hi.tzinfo is None else hi.astimezone(zone)
            if lo < end and hi > start:
                relevant.append({**ev, "start": lo.isoformat(), "end": hi.isoformat()})
        except (ValueError, KeyError, TypeError):
            continue
    return {
        "status": "available" if complete else "unknown",
        "timezone": timezone,
        "checked_at": datetime.now(UTC).isoformat(),
        "checked_sources": checked,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_minutes": duration,
        "slots": slots,
        "events": sorted(relevant, key=lambda ev: ev["start"]),
        "warnings": warnings,
    }


def read_google_events(home, start: datetime, end: datetime) -> list[dict]:
    from googleapiclient.discovery import build

    from pu.tools.google_calendar import DEFAULT_SCOPES, _load_credentials

    credentials = _load_credentials(home, DEFAULT_SCOPES)
    if credentials is None:
        raise ValueError("Google credential không hợp lệ.")
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    events, token = [], None
    while True:
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
                pageToken=token,
            )
            .execute()
        )
        for ev in response.get("items", []):
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue
            lo, hi = ev.get("start", {}), ev.get("end", {})
            events.append(
                {
                    "id": ev["id"],
                    "title": ev.get("summary", "Cuộc hẹn"),
                    "start": lo.get("dateTime") or lo.get("date"),
                    "end": hi.get("dateTime") or hi.get("date"),
                    "attendees": ", ".join(a.get("email", "") for a in ev.get("attendees", [])),
                    "source": "Google Calendar",
                }
            )
        token = response.get("nextPageToken")
        if not token:
            return events
        if len(events) > 5000:
            raise ValueError("Lịch vượt giới hạn đọc an toàn.")


def partner_availability(home, workspace, **options):
    """Compute against every busy interval, then show only partner meetings."""
    if not options:
        options = week_options()
    result = read_availability(home, **options)
    return filter_partner_events(result, workspace)


def filter_partner_events(result, workspace):
    import re
    from email.utils import getaddresses
    from urllib.parse import urlparse
    result = {**result}
    domain = (urlparse(workspace["website"]).hostname or "").lower().removeprefix("www.")
    contact = workspace["contact"].lower()
    company = workspace["company"].lower()
    def matches(event):
        emails = {email.lower() for _, email in getaddresses([str(event.get("attendees", ""))])}
        if contact and contact in emails:
            return True
        if domain and any(email.rsplit("@", 1)[-1] == domain for email in emails):
            return True
        return len(company) >= 4 and bool(re.search(r"(?<!\w)" + re.escape(company) + r"(?!\w)", event["title"], re.IGNORECASE))
    result["events"] = [ev for ev in result["events"] if matches(ev)]
    result["coverage"] = "Chỉ lịch của bạn; chưa kiểm tra lịch của đối tác."
    return result


def make_week_tool(home):
    import json

    from pu.tools.registry import Tool

    def read_week_availability():
        result = read_availability(home, **week_options())
        result["coverage"] = "Lịch của bạn từ hiện tại đến hết Chủ nhật tuần này; chưa biết lịch của đối tác."
        return json.dumps(result, ensure_ascii=False)

    return Tool(name="read_week_availability", description="Read actual busy intervals and 45-minute free appointments for the rest of the current Vietnam calendar week. Explicit local/Google coverage and unknown failures.",
        input_schema={"type":"object","properties":{}}, fn=read_week_availability,
        capability="calendar:read", required_scope="calendar:read", side_effect=False, external=True)
