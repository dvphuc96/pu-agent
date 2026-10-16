"""Regression tests for the final v2 quality gate and semantic statuses."""
from __future__ import annotations

import json

from evals.deterministic.test_report_quality_v2 import _materialize
from pu.core.interfaces.llm import LLMResponse, LLMUsage
from pu.evaluation import DefaultEvaluationHarness, DeterministicReportEvaluator, LLMReportJudge
from pu.reporting.models import ReportClaim


class FakeJudgeProvider:
    provider_id = "offline-quality-judge"

    def __init__(self, payload: str):
        self.payload = payload
        self.requests = []

    def complete(self, request, on_text=None):
        self.requests.append(request)
        return LLMResponse(text=self.payload, usage=LLMUsage(10, 10), finish_reason="stop")


def _judge_payload(score=8, *, complete=True, claim_verdicts=None, field_verdicts=None, factual_support=None) -> str:
    criteria = {"factual_support": score, "completeness": score, "relevance": score, "meeting_readiness": score, "nondup": score}
    if not complete:
        criteria.pop("nondup")
    if factual_support is not None:
        criteria["factual_support"] = factual_support
    return json.dumps({"score": score, "reason": "offline fixture", "criteria": criteria,
                       "claim_verdicts": claim_verdicts or [], "field_verdicts": field_verdicts or []})


def test_every_v2_report_requires_semantic_review_before_final_release():
    report = _materialize("good")
    preliminary = DeterministicReportEvaluator().evaluate(report)
    assert preliminary.release_allowed is True
    assert preliminary.status_details["semantic_review"] == "unavailable"
    final = DefaultEvaluationHarness(deterministic=DeterministicReportEvaluator(), judge=None).evaluate(report)
    assert final.release_allowed is False
    assert final.status_details["semantic_review"] == "unavailable"


def test_low_valid_judge_is_failed_and_is_retryable_by_orchestrator():
    provider = FakeJudgeProvider(_judge_payload(4))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(_materialize("good"))
    assert result.release_allowed is False
    assert result.status_details["semantic_review"] == "failed"
    assert result.judge is not None and result.judge.score == 4


def test_high_judge_cannot_override_numeric_grounding_failure():
    provider = FakeJudgeProvider(_judge_payload(9))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(_materialize("numeric_mismatch"))
    assert result.status_details["semantic_review"] == "passed"
    assert result.release_allowed is False
    assert result.metrics.unsupported_claims >= 1


def test_bad_or_incomplete_judge_is_distinct_from_a_low_score():
    provider = FakeJudgeProvider(_judge_payload(8, complete=False))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(_materialize("good"))
    assert result.release_allowed is False
    assert result.status_details["semantic_review"] == "incomplete"

    broken = FakeJudgeProvider("not json")
    unavailable = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(broken, "offline"),
    ).evaluate(_materialize("good"))
    assert unavailable.release_allowed is False
    assert unavailable.status_details["semantic_review"] == "unavailable"


def test_passing_judge_is_the_only_v2_final_pass_and_sees_whole_prose():
    provider = FakeJudgeProvider(_judge_payload(8))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(_materialize("good"))
    assert result.release_allowed is True
    assert result.status_details["semantic_review"] == "passed"
    prompt = provider.requests[0].messages[0]["content"]
    assert "Acme is an AI company." in prompt
    assert "Prepare an AI pilot discussion." in prompt


def test_explicit_claim_verdict_can_resolve_translation_floor_only():
    provider = FakeJudgeProvider(_judge_payload(8, claim_verdicts=[
        {"id": "C3", "supported": True, "reason": "English source and Vietnamese paraphrase agree."},
    ]))
    report = _materialize("translated_claim")
    deterministic = DeterministicReportEvaluator().evaluate(report)
    assert deterministic.release_allowed is False
    assert "C3" in deterministic.status_details["soft_pending_claims"]
    assert deterministic.status_details["hard_failure"] is False
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(report)
    assert result.status_details["semantic_review"] == "passed"
    assert result.metrics.source_coverage == 1.0
    assert result.release_allowed is True


def test_partial_claim_verdicts_cannot_release_one_of_six_unresolved():
    report = _materialize("translated_claim")
    report.claims.extend([
        ReportClaim("C4", "Acme đã công bố hoạt động thử nghiệm AI cho thị trường bán lẻ.", "recent_updates", ("S4",)),
        ReportClaim("C5", "Acme offers retail products.", "company", ("S2",)),
        ReportClaim("C6", "Alice requested Q4 planning.", "request", ("S1",)),
    ])
    provider = FakeJudgeProvider(_judge_payload(8, claim_verdicts=[
        {"id": "C3", "supported": True, "reason": "Explicit translation match."},
    ]))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(report)
    assert result.status_details["semantic_review"] == "passed"
    assert result.metrics.source_coverage == round(5 / 6, 4)
    assert result.metrics.unsupported_claims == 1
    assert result.status_details["unresolved_claims"] == ["C4"]
    assert result.release_allowed is False


def test_negative_claim_verdict_blocks_even_when_deterministic_floor_passes():
    provider = FakeJudgeProvider(_judge_payload(9, claim_verdicts=[
        {"id": "C2", "supported": False, "reason": "The cited source does not support this wording."},
    ]))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(_materialize("good"))
    assert result.status_details["semantic_review"] == "passed"
    assert result.status_details["semantic_negative_claims"] == ["C2"]
    assert result.release_allowed is False


def test_field_verdict_recomputes_meeting_readiness():
    report = _materialize("good")
    for entry in report.metadata["information_coverage"]:
        if entry["field"] == "calendar":
            entry["summary"] = "Lịch trống thứ Sáu lúc 10 giờ"
    provider = FakeJudgeProvider(_judge_payload(8, field_verdicts=[
        {"field": "calendar", "supported": True, "reason": "Vietnamese calendar paraphrase."},
    ]))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(report)
    assert result.status_details["semantic_review"] == "passed"
    assert result.metrics.meeting_readiness == 1.0
    assert result.release_allowed is True


def test_factual_support_keeps_seven_point_floor():
    provider = FakeJudgeProvider(_judge_payload(9, factual_support=6))
    result = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "offline"),
    ).evaluate(_materialize("good"))
    assert result.status_details["semantic_review"] == "failed"
    assert result.release_allowed is False
