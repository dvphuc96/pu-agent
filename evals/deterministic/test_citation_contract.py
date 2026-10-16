"""Cross-format contract tests for deterministic report citations."""

from __future__ import annotations

import zipfile
from io import BytesIO

from pu.reporting.citations import build_citation_presentation, canonical_public_url
from pu.reporting.docx import render_docx
from pu.reporting.evidence import StructuredReportBuilder
from pu.reporting.models import ReportSource, StructuredReport
from pu.reporting.render import ReportRenderer


def source(source_id: str, url: str, title: str | None = None, *, derived: bool = False) -> ReportSource:
    return ReportSource(
        id=source_id,
        stage="web",
        source_type="agent_output" if derived else "web",
        title=title or f"Nguồn {source_id}",
        excerpt=f"Thông tin hỗ trợ {source_id}",
        url=url,
        domain="example.test",
        external=True,
        derived=derived,
    )


def test_citation_numbers_dedup_first_seen_and_cover_missing_information():
    report = StructuredReport(
        report_id="citation-contract",
        workflow_id="wf",
        title="Đối tác",
        executive_summary="Tóm tắt [W1][W2].",
        sections={},
        claims=[],
        sources=[
            source("W1", "https://B.example/company/"),
            source("W2", "https://a.example/company#section"),
            source("W3", "https://a.example/company/", "Bản sao"),
            source("W9", "https://z.example/missing", "Nguồn thứ chín"),
            source("Wd", "https://derived.example", derived=True),
        ],
        missing_information=["Cần xác minh thêm [W9] và [Wd]."],
        schema_version=2,
    )
    presentation = build_citation_presentation(report)
    assert presentation["W1"]["number"] == 1
    assert presentation["W2"]["number"] == presentation["W3"]["number"] == 2
    assert presentation["W9"]["number"] == 3
    assert presentation["W3"]["anchor"] == presentation["W2"]["anchor"]
    assert presentation["Wd"]["clickable"] is False
    assert presentation["Wd"]["number"] is None

    html = ReportRenderer().render_html(report)
    print_html = ReportRenderer().render_print_html(report)
    markdown = StructuredReportBuilder.render_markdown(report)
    with zipfile.ZipFile(BytesIO(render_docx(report.to_dict()))) as archive:
        docx_xml = archive.read("word/document.xml").decode()
        docx_rels = archive.read("word/_rels/document.xml.rels").decode()

    # Every format uses the same numbers, and a citation only in missing info
    # still gets a visible source target even after the eight-source fold.
    for number in ("1", "2", "3"):
        assert f"Nguồn {number}" in html or f"[{number}]" in html
        assert f"[{number}]" in markdown
        assert f"[{number}]" in docx_xml
        assert f"[{number}]" in print_html
    assert "https://z.example/missing" in html
    assert "https://z.example/missing" in print_html
    assert "https://z.example/missing" in markdown
    assert "Target=\"https://z.example/missing\"" in docx_rels
    assert "src-Wd" not in html
    assert "derived.example" not in docx_rels


def test_citation_urls_and_titles_are_escaped_and_invalid_urls_are_not_links():
    report = StructuredReport(
        report_id="citation-escaping",
        workflow_id="wf",
        title="Đối tác <demo>",
        executive_summary="Nguồn [W1] và lỗi [W2].",
        sections={},
        claims=[],
        sources=[
            source("W1", "https://example.test/a?x=1&y=2", "Trang <chính thức>"),
            source("W2", "javascript:alert(1)", "Không hợp lệ"),
        ],
    )
    html = ReportRenderer().render_html(report)
    assert "Trang &lt;chính thức&gt;" in html
    assert "https://example.test/a?x=1&amp;y=2" in html
    assert "javascript:alert" not in html
    assert "Nguồn chưa xác minh" in html


def test_canonical_key_normalizes_aliases_but_keeps_first_actual_href():
    first_href = "https://WWW.example.test:443/company?b=2&utm_source=mail&a=1#team"
    report = StructuredReport(
        report_id="citation-url-contract",
        workflow_id="wf",
        title="Đối tác",
        executive_summary="[W1][W2][W3]",
        sections={},
        claims=[],
        sources=[
            source("W1", first_href),
            source("W2", "https://example.test/company?a=1&b=2"),
            source("W3", "https://user:secret@example.test/company"),
        ],
    )
    presentation = build_citation_presentation(report)

    assert canonical_public_url(first_href) == "https://example.test/company?a=1&b=2"
    assert presentation["W1"]["number"] == presentation["W2"]["number"] == 1
    assert presentation["W1"]["url"] == first_href
    assert presentation["W2"]["url"] == first_href
    assert presentation["W3"]["number"] is None
    assert presentation["W3"]["clickable"] is False
