"""Calendar refresh reuses public evidence without mutating the reviewed report."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from evals.deterministic.test_inbox_chat import build, seed
from pu.reporting.cache import company_evidence_is_current, reusable_company_sources
from pu.reporting.policy import CURRENT_RUBRIC_VERSION


def test_calendar_expiry_refreshes_report_without_refetching_company(tmp_path, monkeypatch):
    app, _, collected = build(tmp_path, monkeypatch)
    seed(app)
    first = app.respond("Hôm nay có email gì mới?")
    ref = first.reports[0]
    original = deepcopy(app.conn.execute(
        "SELECT payload_json,content_hash FROM workspace_reports WHERE report_id=?", (ref["report_id"],)
    ).fetchone()[:])
    monkeypatch.setattr("pu.reporting.availability.snapshot_is_current", lambda snapshot: False)
    second = app.respond("Hôm nay có email gì mới?")
    assert len(collected) == 1
    assert first.reports[0]["report_id"] != second.reports[0]["report_id"]
    stored = app.conn.execute(
        "SELECT payload_json,content_hash FROM workspace_reports WHERE report_id=?", (ref["report_id"],)
    ).fetchone()
    assert tuple(stored) == original
    payload = json.loads(app.conn.execute(
        "SELECT payload_json FROM workspace_reports WHERE report_id=?", (second.reports[0]["report_id"],)
    ).fetchone()[0])
    assert payload["metadata"]["company_evidence_cache_reused"] is True
    assert payload["metadata"]["performance"]["evidence_cache_hit"] is True
    assert payload["metadata"]["company_evidence_checked_at"] == json.loads(original[0])["metadata"]["company_evidence_checked_at"]
    app.close()


def test_company_evidence_expiry_causes_new_collection(tmp_path, monkeypatch):
    app, _, collected = build(tmp_path, monkeypatch)
    seed(app)
    app.respond("Hôm nay có email gì mới?")
    monkeypatch.setattr("pu.reporting.cache.company_evidence_is_current", lambda payload: False)
    app.respond("Hôm nay có email gì mới?")
    assert len(collected) == 2
    app.close()


def test_cache_ttl_and_policy_are_independent_of_calendar():
    current = datetime(2026, 10, 16, 10, tzinfo=UTC)
    payload = {"metadata": {"company_evidence_checked_at": current.isoformat()},
               "evaluation": {"rubric_version": CURRENT_RUBRIC_VERSION}}
    assert company_evidence_is_current(payload, reference=current + timedelta(minutes=16))
    assert not company_evidence_is_current(payload, reference=current + timedelta(hours=7))
    assert not company_evidence_is_current(payload, reference=current - timedelta(seconds=1))
    payload["evaluation"]["rubric_version"] = "old"
    assert not company_evidence_is_current(payload, reference=current)


def test_cache_does_not_reuse_disabled_sources_or_old_calendar():
    source = {"id": "W1", "title": "Website", "source_type": "web", "excerpt": "Public fact",
              "metadata": {"workspace_source_id": "src-web"}}
    payload = {"sources": [source, {**source, "source_type": "calendar"}]}
    assert reusable_company_sources(payload, set()) == []
    reused = reusable_company_sources(payload, {"src-web"})
    assert len(reused) == 1
    assert reused[0]["source_type"] == "web"
