"""Offline contracts for the v2 report content schema and rendering policy."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

from pu.reporting.docx import render_docx
from pu.reporting.evidence import EvidenceBundle, StructuredReportBuilder
from pu.reporting.models import ReportSource, StructuredReport
from pu.reporting.render import ReportRenderer


def _source(source_id: str = "S1", *, excerpt: str = "Website giới thiệu doanh nghiệp.") -> ReportSource:
    return ReportSource(
        id=source_id,
        stage="web",
        source_type="web",
        title="Official site",
        excerpt=excerpt,
        url=f"https://acme.example/{source_id}",
        domain="acme.example",
        external=True,
    )


def test_legacy_report_instance_is_not_silently_upgraded():
    report = StructuredReport(
        report_id="report-v2",
        workflow_id="wf",
        title="Acme",
        executive_summary="Tóm tắt",
        sections={},
        claims=[],
        sources=[],
    )
    assert report.schema_version == 1
    assert "quality_policy_version" not in report.metadata
    assert "information_coverage" not in report.metadata


def test_assemble_v2_coverage_drops_invalid_refs_and_requires_not_applicable_reason():
    builder = StructuredReportBuilder()
    evidence = EvidenceBundle((_source(),))
    raw = json.dumps({
        "title": "Acme",
        "executive_summary": "Acme [S1].",
        "sections": {"company": "Acme [S1]."},
        "information_coverage": [
            {"field": "website", "status": "supported", "source_ids": ["S1", "S999"], "summary": "Có website"},
            {"field": "scale", "status": "not_applicable", "source_ids": [], "summary": ""},
        ],
        "claims": [], "missing_information": [], "contradictions": [],
    })
    report = builder.assemble(raw, evidence, workflow_id="wf")
    assert report.schema_version == 2
    assert report.metadata["quality_policy_version"] == "report-quality-v2"
    assert {item["field"] for item in report.metadata["information_coverage"]} == {
        "website", "sector", "products", "scale", "markets", "digital_context",
        "contact", "request", "meeting_preparation", "calendar",
    }
    coverage = {item["field"]: item for item in report.metadata["information_coverage"]}
    assert coverage["website"]["status"] == "supported"
    assert coverage["website"]["source_ids"] == ["S1"]
    assert coverage["website"]["invalid_source_ids"] == ["S999"]
    from pu.evaluation import DeterministicReportEvaluator
    assert DeterministicReportEvaluator().evaluate(report).metrics.invalid_source_refs == 1
    assert coverage["scale"]["status"] == "not_found"


def test_prompt_selects_relevant_excerpt_and_requests_new_sections():
    long = "Mở đầu không liên quan. " * 200 + " Quy mô nhà máy có 3 cơ sở và 1200 nhân viên."
    source = _source(excerpt=long)
    source = ReportSource(**{**source.__dict__, "metadata": {"information_fields": ["scale"]}})
    prompt = StructuredReportBuilder().generation_prompt(EvidenceBundle((source,)))
    assert "3 cơ sở" in prompt
    assert "company_scale" in prompt and "business_context" in prompt and "meeting_preparation" in prompt


def test_render_public_source_panel_is_compact_but_keeps_cited_extra():
    sources = [_source(f"S{i}", excerpt=f"Nguồn {i}") for i in range(1, 11)]
    report = StructuredReport(
        report_id="report-render",
        workflow_id="wf",
        title="Acme",
        executive_summary="Sự kiện có nguồn [S10].",
        sections={},
        claims=[],
        sources=sources,
    )
    out = ReportRenderer().render_html(report)
    assert "Số nguồn: 9" in out  # cited public extra is retained in the reader
    assert "https://acme.example/S1" in out


def test_render_prose_turns_source_ids_into_numbered_safe_citation_links():
    report = StructuredReport(
        report_id="report-citations",
        workflow_id="wf",
        title="Acme",
        executive_summary="Nội dung [W1][W2]. <script>alert(1)</script>",
        sections={},
        claims=[],
        sources=[_source("W1"), _source("W2")],
    )
    out = ReportRenderer().render_html(report)
    assert "href='#src-W1'" in out and "href='#src-W2'" in out
    assert "aria-label='Nguồn 1'" in out and "aria-label='Nguồn 2'" in out
    assert ">[1]</a> <a" in out  # chips cannot visually collapse into “12”
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_render_hides_derived_cards_and_preserves_calendar_snapshot_context():
    report = StructuredReport(
        report_id="report-calendar",
        workflow_id="wf",
        title="Acme",
        executive_summary="Tóm tắt",
        sections={},
        claims=[],
        sources=[_source(), ReportSource(
            id="S2", stage="web", source_type="agent_output", title="Internal synthesis",
            excerpt="Should not be shown", derived=True,
        )],
        metadata={
            "calendar_free_slots": [{"label": "Thứ sáu", "windows": ["09:00–09:45"]}],
            "availability": {
                "checked_at": "2026-10-16T09:00:00+07:00",
                "timezone": "Asia/Ho_Chi_Minh",
                "scope": "lịch local; chưa biết lịch đối tác",
                "checked_sources": ["local calendar"],
            },
        },
    )
    for out in (ReportRenderer().render_html(report), ReportRenderer().render_print_html(report)):
        assert "Internal synthesis" not in out
        assert "2026-10-16T09:00:00+07:00" in out
        assert "Asia/Ho_Chi_Minh" in out
        assert "chưa biết lịch đối tác" in out


def test_docx_sources_are_human_title_hyperlinks_and_skip_derived():
    payload = {
        "title": "Acme",
        "executive_summary": "Tóm tắt",
        "sections": {},
        "claims": [],
        "sources": [
            {"id": "W1", "title": "Trang chính thức Acme", "url": "https://acme.example", "derived": False},
            {"id": "S2", "title": "Agent synthesis", "url": "https://internal.invalid", "derived": True},
        ],
    }
    with zipfile.ZipFile(BytesIO(render_docx(payload))) as archive:
        document = archive.read("word/document.xml").decode()
        rels = archive.read("word/_rels/document.xml.rels").decode()
    assert "Trang chính thức Acme" in document
    assert "Agent synthesis" not in document
    assert "Target=\"https://acme.example\"" in rels

