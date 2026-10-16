"""Report snapshots expire without changing the immutable approval content."""
from datetime import UTC, datetime, timedelta

import pytest

from pu.reporting.availability import snapshot_is_current, week_options
from pu.tools.report_delivery import report_can_release


@pytest.mark.parametrize("age,slot_offset,expected", [
    (0, 3600, True), (899, 3600, True), (901, 3600, False),
    (-1, 3600, False), (0, -1, False),
])
def test_expired_future_dated_or_past_slot_cannot_be_reused(age, slot_offset, expected):
    reference = datetime(2026, 10, 16, 1, tzinfo=UTC)
    snapshot = {
        "checked_at": (reference - timedelta(seconds=age)).isoformat(),
        "slots": [{"start": (reference + timedelta(seconds=slot_offset)).isoformat()}],
    }
    assert snapshot_is_current(snapshot, reference=reference) is expected


def test_week_scope_uses_vietnam_day_and_stops_at_sunday():
    # 18:00 UTC Thursday is 01:00 Friday in Vietnam.
    options = week_options(reference=datetime(2026, 10, 15, 18, tzinfo=UTC))
    assert options["reference"].weekday() == 4
    assert options["days"] == 3


def test_old_autoreport_cannot_claim_new_gate_by_retaining_release_boolean():
    payload = {
        "evaluation": {"release_allowed": True},
        "metadata": {"require_semantic_review": True,
                     "evidence_policy_version": "company-boundary-v1"},
    }
    assert not report_can_release(payload)
    payload["metadata"]["quality_policy_version"] = "report-quality-v2"
    assert not report_can_release(payload)
    payload["evaluation"]["quality_policy_version"] = "report-quality-v2"
    assert not report_can_release(payload)
    payload["evaluation"].update(rubric_version="report-quality-rubric-v3", evaluated=True,
                                 status_details={"semantic_review": "passed"})
    assert report_can_release(payload)


def test_invalid_timezone_or_unzoned_snapshot_is_not_current():
    assert not snapshot_is_current({"checked_at": "2026-10-16T09:00:00", "slots": []})
    assert not snapshot_is_current({"checked_at": "invalid", "slots": []})


def test_revision_preserves_schema_and_does_not_mutate_reviewed_report():
    from copy import deepcopy

    from pu.reporting.revisions import revise_report

    original = {
        "report_id": "original", "workflow_id": "workflow", "schema_version": 2,
        "title": "Acme", "executive_summary": "Summary", "sections": {},
        "claims": [], "sources": [], "metadata": {"quality_policy_version": "report-quality-v2"},
    }
    snapshot = deepcopy(original)
    revised = revise_report(original, {"title": "Acme — revised"})
    assert revised.schema_version == 2
    assert revised.metadata["quality_policy_version"] == "report-quality-v2"
    assert original == snapshot
