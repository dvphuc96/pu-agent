"""Deterministic coverage for readable report rendering (HTML/PDF)."""

from __future__ import annotations

import pytest

from pu.evaluation.models import DeterministicMetrics, ReportEvaluation
from pu.reporting.models import ReportClaim, ReportSource, StructuredReport
from pu.reporting.render import ReportRenderer


def make_report(**overrides) -> StructuredReport:
    base = {
        "report_id": "report-test-0001",
        "workflow_id": "wf-1",
        "title": "ProtonX Brief <demo>",
        "executive_summary": "Đối tác AI tại Việt Nam.\nDòng thứ hai.",
        "sections": {
            "Company Overview": "White-label AI brokerage",
            "Products": "Courses <&> bootcamps",
        },
        "claims": [ReportClaim(id="c1", text="claim", section="s", source_ids=("S1",))],
        "sources": [ReportSource(
            id="S1", stage="web", source_type="web", title="Site <Home>",
            excerpt="", url="https://protonx.co/", domain="protonx.co",
        )],
        "missing_information": ["No revenue data"],
    }
    base.update(overrides)
    return StructuredReport(**base)


def make_evaluation(release_allowed: bool = True, overall: float = 0.81) -> ReportEvaluation:
    return ReportEvaluation(
        report_id="report-test-0001",
        gate_status="pass" if release_allowed else "fail",
        release_allowed=release_allowed,
        metrics=DeterministicMetrics(
            completeness=0.9, source_coverage=0.85, source_diversity=0.5,
            freshness=0.7, unsupported_claims=0, invalid_source_refs=0,
            contradictions=0, source_count=6, unique_domains=4,
            official_or_internal_sources=2, freshness_known_sources=3,
            total_claims=7, overall=overall,
        ),
    )


def test_render_html_contains_sections_sources_and_escapes_untrusted():
    out = ReportRenderer().render_html(make_report(), make_evaluation())
    assert "ProtonX Brief &lt;demo&gt;" in out          # title escaped
    assert "Company Overview" in out
    assert "Courses &lt;&amp;&gt; bootcamps" in out     # section body escaped
    assert "Đối tác AI tại Việt Nam.<br/>" in out       # newline → <br/>
    assert "https://protonx.co/" in out and "Site &lt;Home&gt;" in out
    assert "Missing Information" in out and "No revenue data" in out
    assert "<script>" not in out


def test_render_html_verdict_box_reflects_gate():
    ok = ReportRenderer().render_html(make_report(), make_evaluation(True, 0.81))
    assert "verdict pass" in ok and "81%" in ok
    bad = ReportRenderer().render_html(make_report(), make_evaluation(False, 0.24))
    assert "verdict fail" in bad and "24%" in bad


def test_render_html_without_evaluation_omits_verdict():
    out = ReportRenderer().render_html(make_report(), None)
    assert "class='verdict" not in out   # the CSS block may still define the style
    assert "Executive Summary" in out
    assert "Completeness" not in out     # no evaluation → no stat row either


def test_render_html_notebook_two_pane_sources_panel_and_stat_row():
    """NotebookLM look: left "Nguồn" panel with source cards, right document
    pane, plus the evaluation stat row."""
    out = ReportRenderer().render_html(make_report(), make_evaluation())
    assert "<div class='app'>" in out
    assert "class='sources-panel'" in out and "class='doc'" in out
    assert ">Nguồn<" in out
    assert "Số nguồn: 1" in out                    # fixture ships one source
    assert "class='src-card'" in out and "id='src-S1'" in out
    assert "class='src-icon'" in out
    assert "Completeness" in out and "90%" in out
    assert "Source coverage" in out and "85%" in out
    assert "Freshness" in out and "70%" in out


def test_render_html_claims_render_numbered_citation_chips():
    """Claims carry source_ids — each claim line ends with NotebookLM-style
    numbered chips that anchor to the source cards in the panel."""
    report = make_report(claims=[
        ReportClaim(id="c1", text="claim", section="Products", source_ids=("S1",)),
    ])
    out = ReportRenderer().render_html(report, make_evaluation())
    assert "class='claim'" in out
    assert "href='#src-S1'" in out
    assert ">1</a>" in out                        # S1 → chip label "1"


def test_render_print_html_is_flat_and_keeps_sources_table_for_pdf():
    """xhtml2pdf has no grid: the PDF pipeline renders a separate flat,
    single-column document with the provenance table and its anchors."""
    out = ReportRenderer().render_print_html(make_report(), make_evaluation())
    assert "grid" not in out and "sources-panel" not in out
    assert "<table>" in out and "name='src-S1'" in out
    assert "class='verdict pass'" in out


def test_free_slots_render_in_both_layouts():
    report = make_report(metadata={"calendar_free_slots": [
        {"date": "2026-10-13", "label": "T3 · 13/10", "windows": ["09:00–11:30"]},
    ]})
    for out in (
        ReportRenderer().render_html(report, make_evaluation()),
        ReportRenderer().render_print_html(report, make_evaluation()),
    ):
        assert "Lịch trống đề xuất" in out
        assert "T3 · 13/10" in out and "09:00–11:30" in out


def test_render_html_missing_information_uses_warn_callout():
    with_gap = ReportRenderer().render_html(make_report(), make_evaluation())
    assert "class='warn'" in with_gap
    assert ReportRenderer().render_print_html(make_report(), make_evaluation()).count("warn") >= 1
    clean = ReportRenderer().render_html(
        make_report(missing_information=[]), make_evaluation()
    )
    assert "class='warn'" not in clean   # the CSS block still defines the style


def test_render_source_links_and_type_tags_in_both_layouts():
    report = make_report(sources=[
        ReportSource(
            id="S1", stage="web", source_type="web", title="Site <Home>",
            excerpt="AI education in Vietnam", url="https://protonx.co/",
            domain="protonx.co",
        ),
    ])
    browser = ReportRenderer().render_html(report, make_evaluation())
    assert "<a href='https://protonx.co/'" in browser    # panel title is a link
    assert "AI education in Vietnam" in browser          # snippet shown
    print_out = ReportRenderer().render_print_html(report, make_evaluation())
    assert "<td>web</td>" in print_out
    assert "<a href='https://protonx.co/'>protonx.co</a>" in print_out


def test_render_html_has_no_local_font_face_for_browser_cleanliness():
    """The 'Viet' TTF only exists for xhtml2pdf; a browser served over http://
    blocks the file:// font and logs an error. render_html must stay clean —
    the font face is injected at write_pdf time instead."""
    out = ReportRenderer().render_html(make_report(), make_evaluation())
    assert "@font-face" not in out
    assert "Arial Unicode" not in out


def test_write_pdf_injects_unicode_font_face_into_source(tmp_path, monkeypatch):
    import pu.reporting.render as render_mod

    fake = tmp_path / "DejaVuSans.ttf"
    fake.write_bytes(b"fake-font")
    monkeypatch.setattr(render_mod, "_FONT_CANDIDATES", (str(fake),))
    captured: dict = {}

    class FakeStatus:
        err = 0

    def fake_create_pdf(src=None, dest=None, encoding=None):
        captured["src"] = src
        return FakeStatus()

    monkeypatch.setattr(render_mod.pisa, "CreatePDF", fake_create_pdf)
    render_mod.ReportRenderer().write_pdf(
        "<html><head><style>x</style></head></html>", tmp_path / "out.pdf"
    )
    assert "font-family: 'Viet'" in captured["src"]
    assert fake.as_uri() in captured["src"]


def test_font_face_prefers_first_existing_candidate(tmp_path, monkeypatch):
    import pu.reporting.render as render_mod

    fake = tmp_path / "Arial Unicode.ttf"
    fake.write_bytes(b"fake-font")
    monkeypatch.setattr(render_mod, "_FONT_CANDIDATES", (str(fake),))
    css = render_mod._font_face()
    assert "font-family: 'Viet'" in css
    assert fake.as_uri() in css


def test_write_pdf_returns_none_when_library_missing(monkeypatch, tmp_path):
    import pu.reporting.render as render_mod

    monkeypatch.setattr(render_mod, "pisa", None)
    out = tmp_path / "x.pdf"
    assert render_mod.ReportRenderer().write_pdf("<html><body>hi</body></html>", out) is None
    assert not out.exists()


def test_write_pdf_produces_valid_pdf_file(tmp_path):
    out = tmp_path / "report-test.pdf"
    result = ReportRenderer().write_pdf(
        ReportRenderer().render_html(make_report(), make_evaluation()), out
    )
    if result is None:
        pytest.skip("xhtml2pdf not installed (uv run --extra pdf)")
    assert result == out
    assert out.read_bytes()[:4] == b"%PDF"


def test_repository_save_writes_html_alongside_json_and_md(tmp_path):
    from pu.reporting.repository import FileReportRepository

    FileReportRepository(tmp_path).save(make_report(), make_evaluation())
    root = tmp_path / "reports"
    html_text = (root / "report-test-0001.html").read_text(encoding="utf-8")
    assert "ProtonX Brief &lt;demo&gt;" in html_text
    assert "verdict pass" in html_text
    assert (root / "report-test-0001.json").exists()
    assert (root / "report-test-0001.md").exists()


def test_repository_save_skips_pdf_gracefully_with_stub_renderer(tmp_path):
    from pu.reporting.repository import FileReportRepository

    calls: dict = {}

    class StubRenderer:
        def render_html(self, report, evaluation=None):
            return "<html>ok</html>"

        def render_print_html(self, report, evaluation=None):
            return "<html>print</html>"

        def write_pdf(self, html_text, path):
            calls["path"] = path
            calls["html"] = html_text

    FileReportRepository(tmp_path, renderer=StubRenderer()).save(make_report(), None)
    assert calls["path"].name == "report-test-0001.pdf"
    assert calls["html"] == "<html>print</html>"     # PDF uses the print layout
    assert (tmp_path / "reports" / "report-test-0001.html").exists()
    assert not list((tmp_path / "reports").glob("*.pdf"))


def test_repository_save_produces_real_pdf(tmp_path):
    from pu.reporting.repository import FileReportRepository

    FileReportRepository(tmp_path).save(make_report(), make_evaluation())
    pdf = tmp_path / "reports" / "report-test-0001.pdf"
    if not pdf.exists():
        pytest.skip("xhtml2pdf not installed (uv run --extra pdf)")
    assert pdf.read_bytes()[:4] == b"%PDF"
