"""Real evaluator and orchestrator agree on when a report may be repaired."""
from __future__ import annotations

import json

from evals.deterministic.test_inbox_chat import ClassifierProvider, ReportRuntime, build, seed
from pu.core.interfaces.llm import LLMResponse, LLMUsage


class ReviewProvider(ClassifierProvider):
    def __init__(self, *, unavailable=False, low_first=True):
        super().__init__()
        self.reviews = 0
        self.unavailable = unavailable
        self.low_first = low_first

    def complete(self, request, **kwargs):
        if "website_candidates" in request.messages[0]["content"]:
            return super().complete(request, **kwargs)
        self.reviews += 1
        if self.unavailable:
            return LLMResponse(text="not a judge result", usage=LLMUsage(1, 1))
        score = 4 if self.low_first and self.reviews == 1 else 9
        return LLMResponse(
            text=json.dumps({"score": score, "reason": "Repeated prose needs revision" if score == 4 else "Grounded revision",
                             "criteria": {key: score for key in ("factual_support", "completeness", "relevance", "meeting_readiness", "nondup")}}),
            usage=LLMUsage(1, 1),
        )


def setup(tmp_path, monkeypatch, *, unavailable=False):
    runtime = ReportRuntime()
    app, _, _ = build(tmp_path, monkeypatch, runtime=runtime)
    provider = ReviewProvider(unavailable=unavailable)
    app.llm_provider = provider
    app.evaluation_harness.judge.provider = provider
    seed(app)
    return app, runtime, provider


def test_low_judge_score_repairs_once_then_prepares_only_approval(tmp_path, monkeypatch):
    app, runtime, provider = setup(tmp_path, monkeypatch)
    result = app.respond("Hôm nay có email gì mới?")
    assert provider.reviews == 2
    assert len(runtime.requests) == 2  # Only the report model; routing is local.
    assert result.reports[0]["release_allowed"] is True
    payload = json.loads(app.conn.execute("SELECT payload_json FROM workspace_reports").fetchone()[0])
    assert payload["metadata"]["quality_repair_attempts"] == 1
    assert payload["evaluation"]["status_details"]["semantic_review"] == "passed"
    assert app.conn.execute("SELECT COUNT(*) FROM proposed_actions WHERE status='pending'").fetchone()[0] == 1
    assert app.conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0] == 0
    app.close()


def test_unavailable_judge_does_not_rewrite_or_prepare_distribution(tmp_path, monkeypatch):
    app, runtime, provider = setup(tmp_path, monkeypatch, unavailable=True)
    result = app.respond("Hôm nay có email gì mới?")
    assert provider.reviews == 1
    assert len(runtime.requests) == 1
    assert result.reports[0]["release_allowed"] is False
    payload = json.loads(app.conn.execute("SELECT payload_json FROM workspace_reports").fetchone()[0])
    assert payload["metadata"]["quality_repair_attempts"] == 0
    assert payload["evaluation"]["status_details"]["semantic_review"] == "unavailable"
    assert app.conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] == 0
    app.close()


def test_numeric_failure_is_repaired_even_if_judge_overlooks_it(tmp_path, monkeypatch):
    class InitiallyWrongRuntime(ReportRuntime):
        def run(self, request, observer=None):
            result = super().run(request, observer)
            if len(self.requests) == 1:
                payload = json.loads(result.reply)
                payload["claims"][0]["text"] = "Acme có 999999 nhân viên."
                result.reply = json.dumps(payload)
            return result

    runtime = InitiallyWrongRuntime()
    app, _, _ = build(tmp_path, monkeypatch, runtime=runtime)
    provider = ReviewProvider(low_first=False)
    app.llm_provider = provider
    app.evaluation_harness.judge.provider = provider
    seed(app)
    result = app.respond("Hôm nay có email gì mới?")
    assert len(runtime.requests) == 2
    assert provider.reviews == 2
    assert result.reports[0]["release_allowed"] is True
    payload = json.loads(app.conn.execute("SELECT payload_json FROM workspace_reports").fetchone()[0])
    assert payload["metadata"]["quality_repair_attempts"] == 1
    assert payload["evaluation"]["metrics"]["unsupported_claims"] == 0
    assert all("999999" not in claim["text"] for claim in payload["claims"])
    app.close()
