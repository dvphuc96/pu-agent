from __future__ import annotations

import math

from evals.benchmark_report_quality import benchmark, load_records
from pu.reporting.performance import WorkflowPerformance


class Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value


def test_workflow_performance_tracks_stages_reuse_usage_and_finite_duration():
    clock = Clock()
    tracker = WorkflowPerformance(clock=clock)
    tracker.observe("workflow_started", {})
    clock.value = 10.25
    tracker.observe("workflow_stage_started", {"stage": "collect"})
    clock.value = 11.75
    tracker.observe(
        "llm",
        {"provider": "offline", "model": "fixture", "usage": {"in": 12, "out": 8}},
    )
    tracker.observe("workflow_stage_completed", {"stage": "collect"})
    tracker.observe("workflow_stage_reused", {"stage": "synthesis"})
    clock.value = 12.0
    snapshot = tracker.snapshot()

    assert snapshot["scope"] == "research_workflow"
    assert snapshot["attempts"] == 1
    assert snapshot["stages"] == {
        "collect": {"duration_seconds": 1.5, "reused": False},
        "synthesis": {"duration_seconds": 0.0, "reused": True},
    }
    assert snapshot["cache_hit"] is True
    assert snapshot["usage"] == {"input_tokens": 12, "output_tokens": 8}
    assert snapshot["providers"] == ["offline"]
    assert snapshot["models"] == ["fixture"]
    assert all(
        math.isfinite(value)
        for value in [snapshot["duration_seconds"], *[stage["duration_seconds"] for stage in snapshot["stages"].values()]]
    )


def test_measure_and_record_ignore_invalid_numbers():
    clock = Clock()
    tracker = WorkflowPerformance(clock=clock)
    tracker.record("bad-nan", float("nan"))
    tracker.record("bad-inf", float("inf"))
    with tracker.measure("phase"):
        clock.value += 0.5
    snapshot = tracker.snapshot()
    assert snapshot["phases"] == {"phase": 0.5}
    assert "bad-nan" not in snapshot["phases"]
    assert "bad-inf" not in snapshot["phases"]


def test_usage_aliases_are_not_double_counted():
    tracker = WorkflowPerformance(clock=Clock())
    tracker.observe(
        "llm",
        {"usage": {"in": 5, "input_tokens": 5, "out": 2, "output_tokens": 2}},
    )
    assert tracker.snapshot()["usage"] == {"input_tokens": 5, "output_tokens": 2}


def test_benchmark_separates_cold_warm_and_groups_provider_model():
    records = [
        {"scope": "research_workflow", "duration_seconds": 4.0, "cache_hit": False, "provider": "p", "model": "m"},
        {"scope": "research_workflow", "duration_seconds": 2.0, "cache_hit": False, "provider": "p", "model": "m"},
        {"scope": "research_workflow", "duration_seconds": 1.0, "cache_hit": True, "provider": "p", "model": "m"},
        {"scope": "research_workflow", "duration_seconds": 3.0, "cache_hit": True, "provider": "q", "model": "n"},
        {"scope": "research_workflow", "duration_seconds": float("nan"), "cache_hit": False},
    ]
    result = benchmark(records)
    assert result["scope"] == "research_workflow"
    assert result["sample_count"] == 4
    assert result["p50_seconds"] == 2.0
    assert result["p95_seconds"] == 4.0
    assert result["cold"] == {"sample_count": 2, "p50_seconds": 2.0, "p95_seconds": 4.0}
    assert result["warm"] == {"sample_count": 2, "p50_seconds": 1.0, "p95_seconds": 3.0}
    assert result["by_provider_model"]["p/m"]["sample_count"] == 3
    assert "end-to-end" in result["scope_note"]


def test_benchmark_does_not_mix_workflow_and_end_to_end_scopes(tmp_path):
    path = tmp_path / "performance.json"
    path.write_text('{"performance":{"scope":"research_workflow","duration_seconds":"2.5"}}')
    records = load_records(path)
    result = benchmark(records + [{"scope": "end_to_end", "duration_seconds": 100}])
    assert result["sample_count"] == 1
    assert result["excluded_records"] == 1
    assert result["p95_seconds"] == 2.5

