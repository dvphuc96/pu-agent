"""Optional LLM-as-Judge report-quality eval. Deterministic gates remain authoritative."""
from __future__ import annotations

import pytest

from evals.helpers import HAS_KEY
from pu.config import load_settings
from pu.evaluation import LLMReportJudge
from pu.providers.llm.catalog import resolve_provider_settings
from pu.providers.llm.factory import create_llm_provider
from pu.reporting.models import ReportClaim, ReportSource, StructuredReport

pytestmark = pytest.mark.skipif(not HAS_KEY, reason="report judge needs the active provider's API key")


def test_grounded_report_clears_qualitative_judge_floor():
    settings = load_settings()
    resolve_provider_settings(settings)
    provider = create_llm_provider(settings)
    judge = LLMReportJudge(provider, settings.report_judge_model or settings.model)
    report = StructuredReport(
        report_id="judge-fixture", workflow_id="judge-fixture", title="Acme prep",
        executive_summary="Alice at Acme requested a Q4 planning discussion.",
        sections={
            "company": "Acme is an existing customer.", "contacts": "Alice",
            "email_need": "Q4 planning", "meeting_context": "Prepare for discussion",
            "public_research": "Acme announced Product X.", "risks_opportunities": "Confirm scope",
        },
        claims=[
            ReportClaim(id="C1", text="Alice requested Q4 planning.", section="email_need", source_ids=("S1",)),
            ReportClaim(id="C2", text="Acme announced Product X.", section="public_research", source_ids=("S2",)),
        ],
        sources=[
            ReportSource(id="S1", stage="email", source_type="email", title="Inbox", excerpt="Alice: Can we plan Q4 priorities?"),
            ReportSource(id="S2", stage="web", source_type="web", title="Acme news", excerpt="Acme announced Product X.", url="https://acme.example/news", domain="acme.example", external=True),
        ],
    )
    result = judge.evaluate(report)
    assert result is not None
    assert result.score >= 5.0, result.reason
