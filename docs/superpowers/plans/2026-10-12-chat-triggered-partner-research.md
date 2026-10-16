# Chat-Triggered Partner Research + Readable Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent tự chạy research workflow từ trong chat khi phát hiện email đối tác mới, và report xuất ra HTML/PDF cho người dùng đọc thay vì chỉ `.json`/`.md`.

**Architecture:** Tool mới `research_partner` (inject runner qua closure, không phụ thuộc app) đăng ký ở cuối `PU.__init__`; renderer tách lớp `ReportRenderer` (HTML escape toàn bộ nội dung untrusted, PDF qua xhtml2pdf optional extra, fail-open khi chưa cài) tích hợp vào `FileReportRepository.save()`.

**Tech Stack:** Python stdlib (`html`, `pathlib`), `xhtml2pdf` (optional extra `pdf`), pytest qua `uv run --extra dev --extra gmail --extra pdf`.

**Lưu ý môi trường:** Repo KHÔNG phải git repository → các bước "commit" được thay bằng bước verify (chạy test) — không chạy git. Không có `.venv` trong checkout → mọi lệnh test chạy qua `uv run --extra dev --extra gmail --extra pdf python -m pytest -q ...`. Không đọc/hiển thị file `.env`.

**Spec:** `docs/superpowers/specs/2026-10-12-chat-triggered-partner-research-design.md`

---

### Task 1: `ReportRenderer.render_html` — HTML có escape + verdict evaluation

**Files:**
- Create: `pu/reporting/render.py`
- Test: `evals/deterministic/test_report_rendering.py`

- [x] **Step 1.1: Viết test FAIL (fixtures + 3 test render)**

Tạo `evals/deterministic/test_report_rendering.py`:

```python
"""Deterministic coverage for readable report rendering (HTML/PDF)."""

from __future__ import annotations

import pytest

from pu.evaluation.models import DeterministicMetrics, ReportEvaluation
from pu.reporting.models import ReportClaim, ReportSource, StructuredReport
from pu.reporting.render import ReportRenderer


def make_report(**overrides) -> StructuredReport:
    base = dict(
        report_id="report-test-0001",
        workflow_id="wf-1",
        title="ProtonX Brief <demo>",
        executive_summary="Đối tác AI tại Việt Nam.\nDòng thứ hai.",
        sections={
            "Company Overview": "White-label AI brokerage",
            "Products": "Courses <&> bootcamps",
        },
        claims=[ReportClaim(id="c1", text="claim", section="s", source_ids=("S1",))],
        sources=[ReportSource(
            id="S1", stage="web", source_type="web", title="Site <Home>",
            excerpt="", url="https://protonx.co/", domain="protonx.co",
        )],
        missing_information=["No revenue data"],
    )
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
    bad = ReportRenderer().render_html(make_report(), make_evaluation(False, 0.225))
    assert "verdict fail" in bad and "23%" in bad


def test_render_html_without_evaluation_omits_verdict():
    out = ReportRenderer().render_html(make_report(), None)
    assert "verdict" not in out
    assert "Executive Summary" in out
```

- [x] **Step 1.2: Chạy test xác nhận FAIL đúng lý do**

Run: `uv run --extra dev --extra gmail python -m pytest -q evals/deterministic/test_report_rendering.py`
Expected: 3 FAILED với `ModuleNotFoundError: No module named 'pu.reporting.render'`

- [x] **Step 1.3: Implement `pu/reporting/render.py` (chưa có write_pdf — Task 2 thêm)**

```python
"""Readable report rendering: structured report → HTML → PDF.

Every field of a report originates from untrusted web/email evidence, so all
content is HTML-escaped before it reaches the template.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from pu.reporting.models import StructuredReport

# xhtml2pdf is an optional extra (`uv sync --extra pdf`); without it we still
# write .html and skip the .pdf instead of failing the report pipeline.
try:
    from xhtml2pdf import pisa
except ImportError:  # pragma: no cover - depends on installed extras
    pisa = None

# Helvetica's built-in PDF fonts are Latin-1 only — Vietnamese diacritics
# render as black boxes. Any Unicode TTF here fixes that; macOS ships Arial
# Unicode, Linux distros usually have DejaVu or Liberation.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

_CSS = """
body { font-family: 'Viet', Helvetica, sans-serif; font-size: 11pt; color: #1a1a1a; margin: 24pt; }
h1 { font-size: 17pt; border-bottom: 2pt solid #4338ca; padding-bottom: 4pt; }
h2 { font-size: 13pt; color: #4338ca; margin-top: 14pt; }
.meta { color: #555; font-size: 9pt; }
.verdict { border: 1pt solid #999; padding: 6pt; margin: 8pt 0; }
.pass { border-color: #15803d; }
.fail { border-color: #b91c1c; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; }
th, td { border: 0.5pt solid #bbb; padding: 3pt 4pt; text-align: left; vertical-align: top; }
ul { margin-top: 2pt; }
"""


def _font_face() -> str:
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return f"@font-face {{ font-family: 'Viet'; src: url('{path.as_uri()}'); }}"
    return ""


def _br(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br/>")


class ReportRenderer:
    """Deterministic structured-report → HTML/PDF renderer (no LLM)."""

    def render_html(self, report: StructuredReport, evaluation: Any | None = None) -> str:
        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<style>{_font_face()}{_CSS}</style></head><body>",
            f"<h1>{html.escape(report.title)}</h1>",
            f"<p class='meta'>Workflow {html.escape(report.workflow_id)} · "
            f"generated {html.escape(report.generated_at)}</p>",
        ]
        if evaluation is not None:
            parts.append(self._verdict_html(evaluation))
        parts.append("<h2>Executive Summary</h2>")
        parts.append(f"<p>{_br(report.executive_summary)}</p>")
        for name, body in report.sections.items():
            parts.append(f"<h2>{html.escape(name)}</h2><p>{_br(body)}</p>")
        if report.missing_information:
            parts.append("<h2>Missing Information</h2><ul>")
            parts.extend(
                f"<li>{html.escape(item)}</li>" for item in report.missing_information
            )
            parts.append("</ul>")
        if report.sources:
            parts.append("<h2>Sources / Provenance</h2><table>")
            parts.append("<tr><th>ID</th><th>Type</th><th>Title</th><th>URL / Domain</th></tr>")
            for src in report.sources:
                where = src.url or src.domain or ""
                parts.append(
                    "<tr>"
                    f"<td>{html.escape(src.id)}</td>"
                    f"<td>{html.escape(src.source_type)}</td>"
                    f"<td>{html.escape(src.title)}</td>"
                    f"<td>{html.escape(where)}</td>"
                    "</tr>"
                )
            parts.append("</table>")
        parts.append("</body></html>")
        return "".join(parts)

    @staticmethod
    def _verdict_html(evaluation: Any) -> str:
        metrics = getattr(evaluation, "metrics", None)
        overall = getattr(metrics, "overall", None)
        completeness = getattr(metrics, "completeness", None)
        coverage = getattr(metrics, "source_coverage", None)
        allowed = bool(getattr(evaluation, "release_allowed", False))
        gate = getattr(evaluation, "gate_status", "unknown")
        cls = "pass" if allowed else "fail"
        word = "ĐẠT chuẩn chất lượng" if allowed else "CHƯA ĐẠT ngưỡng chất lượng"
        score = f"{overall:.0%}" if isinstance(overall, (int, float)) else "n/a"
        detail = ""
        if isinstance(completeness, (int, float)) and isinstance(coverage, (int, float)):
            detail = f" · completeness {completeness:.0%} · source coverage {coverage:.0%}"
        return (
            f"<p class='verdict {cls}'><b>Evaluation: {word}</b> "
            f"(gate: {html.escape(str(gate))}, overall {score}{detail})</p>"
        )
```

- [x] **Step 1.4: Chạy test xác nhận PASS**

Run: `uv run --extra dev --extra gmail python -m pytest -q evals/deterministic/test_report_rendering.py`
Expected: `3 passed`

---

### Task 2: `write_pdf` + optional extra `pdf` + dò font Unicode

**Files:**
- Modify: `pu/reporting/render.py` (thêm `write_pdf`)
- Modify: `pyproject.toml` (thêm extra `pdf`)
- Test: `evals/deterministic/test_report_rendering.py` (thêm 3 test)

- [x] **Step 2.1: Thêm extra `pdf` vào `pyproject.toml`**

Trong `[project.optional-dependencies]`, sau block `gcal`, thêm:

```toml
# Readable PDF reports (the .html render itself needs no extra).
pdf = [
    "xhtml2pdf>=0.2.11",
]
```

- [x] **Step 2.2: Cài lib**

Run: `uv run --extra dev --extra gmail --extra pdf python -c "import xhtml2pdf; print('ok')"`
Expected: `ok`

- [x] **Step 2.3: Viết test FAIL (3 test pdf/font)**

Append vào `evals/deterministic/test_report_rendering.py`:

```python
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
```

- [x] **Step 2.4: Chạy test xác nhận FAIL đúng lý do**

Run: `uv run --extra dev --extra gmail --extra pdf python -m pytest -q evals/deterministic/test_report_rendering.py`
Expected: 3 FAILED với `AttributeError: ... has no attribute 'write_pdf'` (2 test) và pass/skip cho font test (`_font_face` đã có từ Task 1 — nếu test font pass thì đúng, nó verify hành vi đã có).

- [x] **Step 2.5: Implement `write_pdf` trong `ReportRenderer`**

Thêm method vào class `ReportRenderer` trong `pu/reporting/render.py`:

```python
    def write_pdf(self, html_text: str, path: Path) -> Path | None:
        """Write a PDF next to the .html report; None means "not possible"
        (library extra not installed, or the converter erroled). Never raises:
        a report pipeline must not fail because of presentation."""
        if pisa is None:
            return None
        try:
            with open(path, "wb") as dest:
                status = pisa.CreatePDF(src=html_text, dest=dest, encoding="utf-8")
        except Exception:
            path.unlink(missing_ok=True)
            return None
        if status.err:
            path.unlink(missing_ok=True)
            return None
        return path
```

- [x] **Step 2.6: Chạy test xác nhận PASS**

Run: `uv run --extra dev --extra gmail --extra pdf python -m pytest -q evals/deterministic/test_report_rendering.py`
Expected: `6 passed`

---

### Task 3: Tích hợp renderer vào `FileReportRepository.save()`

**Files:**
- Modify: `pu/reporting/repository.py`
- Test: `evals/deterministic/test_report_rendering.py` (thêm 3 test)

- [x] **Step 3.1: Viết test FAIL**

Append vào `evals/deterministic/test_report_rendering.py`:

```python
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

        def write_pdf(self, html_text, path):
            calls["path"] = path
            return None

    FileReportRepository(tmp_path, renderer=StubRenderer()).save(make_report(), None)
    assert calls["path"].name == "report-test-0001.pdf"
    assert (tmp_path / "reports" / "report-test-0001.html").exists()
    assert not list((tmp_path / "reports").glob("*.pdf"))


def test_repository_save_produces_real_pdf(tmp_path):
    from pu.reporting.repository import FileReportRepository

    FileReportRepository(tmp_path).save(make_report(), make_evaluation())
    pdf = tmp_path / "reports" / "report-test-0001.pdf"
    if not pdf.exists():
        pytest.skip("xhtml2pdf not installed (uv run --extra pdf)")
    assert pdf.read_bytes()[:4] == b"%PDF"
```

- [x] **Step 3.2: Chạy test xác nhận FAIL đúng lý do**

Run: `uv run --extra dev --extra gmail --extra pdf python -m pytest -q evals/deterministic/test_report_rendering.py`
Expected: 3 FAILED với `TypeError: FileReportRepository.__init__() got an unexpected keyword argument 'renderer'` (test 2) và `FileNotFoundError` cho `.html` (test 1, 3)

- [x] **Step 3.3: Implement — sửa `pu/reporting/repository.py`**

```python
"""Local-first report artifact repository."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pu.reporting.models import StructuredReport
from pu.reporting.render import ReportRenderer


class FileReportRepository:
    def __init__(self, home: Path, renderer: ReportRenderer | None = None):
        self.root = home / "reports"
        self.root.mkdir(parents=True, exist_ok=True)
        self.renderer = renderer or ReportRenderer()

    def save(self, report: StructuredReport, evaluation: Any | None = None) -> None:
        payload = report.to_dict()
        if evaluation is not None:
            payload["evaluation"] = evaluation.to_dict() if hasattr(evaluation, "to_dict") else evaluation
        (self.root / f"{report.report_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.root / f"{report.report_id}.md").write_text(report.markdown, encoding="utf-8")
        # Human-readable artifacts: .html always (pure stdlib), .pdf when the
        # optional extra is installed. Presentation must never fail a save.
        html_text = self.renderer.render_html(report, evaluation)
        (self.root / f"{report.report_id}.html").write_text(html_text, encoding="utf-8")
        self.renderer.write_pdf(html_text, self.root / f"{report.report_id}.pdf")

    def list(self, limit: int = 50) -> list[dict]:
        items = []
        for path in sorted(self.root.glob("report-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append(data)
        return items
```

- [x] **Step 3.4: Chạy test xác nhận PASS + không vỡ test cũ**

Run: `uv run --extra dev --extra gmail --extra pdf python -m pytest -q evals/deterministic/test_report_rendering.py evals/deterministic -k "report"`
Expected: tất cả pass (bao gồm các test report có sẵn nếu có)

---

### Task 4: Tool `research_partner`

**Files:**
- Create: `pu/tools/research.py`
- Test: `evals/deterministic/test_research_tool.py`

- [x] **Step 4.1: Viết test FAIL (5 test)**

Tạo `evals/deterministic/test_research_tool.py`:

```python
"""Deterministic coverage for the chat-triggered research tool."""

from __future__ import annotations

from pu.tools.research import make_tool

from evals.deterministic.test_report_rendering import make_evaluation, make_report


class StubResult:
    def __init__(self, structured_report, evaluation=None, workflow_id="wf-42"):
        self.structured_report = structured_report
        self.evaluation = evaluation
        self.report = None
        self.workflow_id = workflow_id
        self.status = "completed"


def test_tool_metadata_schema_and_teaching_description(tmp_path):
    tool = make_tool(lambda prompt, observer=None: StubResult(None), tmp_path)
    assert tool.name == "research_partner"
    assert tool.capability == "research:run"
    assert tool.required_scope == "research:run"
    assert tool.side_effect is False
    assert tool.wants_notify is True
    schema = tool.to_api()
    assert schema["input_schema"]["required"] == ["company"]
    assert set(schema["input_schema"]["properties"]) == {
        "company", "website", "sender_email", "context",
    }
    description = tool.description.lower()
    assert "inbox" in description          # dùng sau khi đọc inbox
    assert "partner" in description or "customer" in description
    assert "approval" in description       # luôn đề nghị human duyệt


def test_tool_builds_structured_prompt_and_forwards_observer(tmp_path):
    seen: dict = {}
    events: list = []

    def runner(prompt, observer=None):
        seen["prompt"] = prompt
        if observer is not None:
            observer("stage_started", {"stage": "email"})
        return StubResult(make_report(), make_evaluation())

    tool = make_tool(runner, tmp_path)
    out = tool.fn(
        company="ProtonX",
        website="https://protonx.co/",
        sender_email="huy@protonx.co",
        context="muốn hẹn buổi giới thiệu giải pháp",
        _notify=lambda kind, ev: events.append((kind, ev)),
    )
    prompt = seen["prompt"]
    assert "ProtonX" in prompt and "https://protonx.co/" in prompt
    assert "huy@protonx.co" in prompt and "giới thiệu giải pháp" in prompt
    assert events == [("stage_started", {"stage": "email"})]
    assert "Research complete" in out


def test_tool_output_summarizes_verdict_paths_and_workflow_id(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report-test-0001.pdf").write_bytes(b"%PDF-fake")
    tool = make_tool(lambda prompt, observer=None: StubResult(make_report(), make_evaluation()), tmp_path)
    out = tool.fn(company="ProtonX")
    assert "PASSED the quality gate (overall 81%)" in out
    assert str(reports / "report-test-0001.pdf") in out   # pdf preferred
    assert "wf-42" in out
    assert "approval" in out.lower()


def test_tool_output_falls_back_to_html_then_md(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report-test-0001.html").write_text("<html>ok</html>", encoding="utf-8")
    tool = make_tool(lambda prompt, observer=None: StubResult(make_report(), make_evaluation()), tmp_path)
    out = tool.fn(company="ProtonX")
    assert str(reports / "report-test-0001.html") in out
    assert ".pdf" not in out


def test_tool_failure_returns_error_guidance_without_raising(tmp_path):
    def boom(prompt, observer=None):
        raise RuntimeError("provider timeout")

    tool = make_tool(boom, tmp_path)
    out = tool.fn(company="ProtonX")
    assert "failed" in out.lower()
    assert "retry" in out.lower() or "resume" in out.lower()
```

- [x] **Step 4.2: Chạy test xác nhận FAIL đúng lý do**

Run: `uv run --extra dev --extra gmail python -m pytest -q evals/deterministic/test_research_tool.py`
Expected: collection error `ModuleNotFoundError: No module named 'pu.tools.research'`

- [x] **Step 4.3: Implement `pu/tools/research.py`**

```python
"""Chat-triggered partner research tool.

Bridges the agent loop to the deterministic multi-agent research workflow:
the model reads the inbox, decides an email is from a new partner, and calls
this tool instead of the user pressing the dashboard's Research button.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pu.tools.registry import Tool

# A long executive summary can blow the answer budget (the max_tokens lesson):
# cap what the tool returns to the chat, the full text is in the report file.
_MAX_SUMMARY_CHARS = 700


def make_tool(research_runner: Callable[..., Any], home: Path) -> Tool:
    """`research_partner` — run the full research workflow for one company.

    ``research_runner(prompt, observer=None)`` is injected (the app passes its
    ``run_research_workflow``), so this module never depends on the app object.
    """

    def research_partner(
        company: str,
        website: str = "",
        sender_email: str = "",
        context: str = "",
        _notify=None,
    ) -> str:
        prompt = f"Research the company '{company}'"
        if website:
            prompt += f" (website: {website})"
        if sender_email:
            prompt += f" (contact: {sender_email})"
        if context:
            prompt += f". Context: {context}"
        prompt += (
            ". Prepare a pre-meeting brief: company introduction, field of "
            "activity, products, recent news, contact information, and points "
            "to note before a meeting."
        )
        try:
            result = research_runner(prompt, observer=_notify)
        except Exception as exc:
            # A failed research run must never kill the chat turn; the
            # workflow checkpoints allow resuming by workflow id.
            return (
                f"Research workflow failed: {exc}. No report was produced. "
                "Tell the user what happened and offer to retry or resume "
                "the workflow."
            )
        report = getattr(result, "structured_report", None)
        workflow_id = getattr(result, "workflow_id", "")
        if report is None:
            return (
                "The workflow finished but produced no structured report "
                f"(workflow {workflow_id}). Tell the user the raw draft is in "
                ".pu/reports/ and offer to retry."
            )
        evaluation = getattr(result, "evaluation", None)
        metrics = getattr(evaluation, "metrics", None)
        overall = getattr(metrics, "overall", None)
        allowed = bool(getattr(evaluation, "release_allowed", False))
        if isinstance(overall, (int, float)):
            verdict = (
                f"PASSED the quality gate (overall {overall:.0%})"
                if allowed
                else f"did NOT pass the quality gate (overall {overall:.0%}) — "
                "be honest with the user about the gaps"
            )
        else:
            verdict = "quality score unavailable"
        summary = (report.executive_summary or "").strip()
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[:_MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
        report_root = home / "reports"
        candidates = [
            report_root / f"{report.report_id}.{ext}" for ext in ("pdf", "html", "md")
        ]
        readable = next((str(p) for p in candidates if p.is_file()), str(candidates[-1]))
        return (
            f"## Research complete: {report.title}\n"
            f"Evaluation: {verdict}\n\n"
            f"{summary}\n\n"
            f"Full report: {readable}\n"
            f"Workflow id: {workflow_id}\n"
            "Next: offer the user the choice to approve forwarding the report "
            "by email or saving it to knowledge — do neither without approval."
        )

    return Tool(
        name="research_partner",
        description=(
            "Run the full multi-agent research workflow (web, company info, "
            "calendar, memory, report generation, evaluation) for ONE company. "
            "Use it right after reading the inbox when you find email from a "
            "new partner or customer that no research brief exists for yet. "
            "Pass the company name plus website/sender taken from the email. "
            "It takes minutes and streams progress events; when it returns, "
            "summarize the executive summary and the evaluation verdict for "
            "the user, point them to the saved report file, and always offer "
            "the approval choice (forward by email / save to knowledge) "
            "before taking any action."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company": {
                    "type": "string",
                    "description": "company or organization name to research",
                },
                "website": {
                    "type": "string",
                    "description": "website URL if known from the email signature",
                },
                "sender_email": {
                    "type": "string",
                    "description": "sender email address from the email",
                },
                "context": {
                    "type": "string",
                    "description": "1-2 sentences: why this partner matters or what the email asks for",
                },
            },
            "required": ["company"],
        },
        fn=research_partner,
        capability="research:run",
        required_scope="research:run",
        side_effect=False,
        external=False,
        wants_notify=True,
    )
```

- [x] **Step 4.4: Chạy test xác nhận PASS**

Run: `uv run --extra dev --extra gmail python -m pytest -q evals/deterministic/test_research_tool.py`
Expected: `5 passed`

---

### Task 5: Wire tool vào app (composition root)

**Files:**
- Modify: `pu/app.py` (import + cuối `__init__`)
- Test: `evals/deterministic/test_research_tool.py` (thêm 1 test)

- [x] **Step 5.1: Viết test FAIL**

Append vào `evals/deterministic/test_research_tool.py`:

```python
def test_application_registers_research_partner_tool(tmp_path):
    from evals.helpers import ScriptedProvider
    from pu.config import Settings
    from pu.core.container import create_application

    settings = Settings(home=tmp_path, api_key="offline", graph_workflows=False)
    app = create_application(settings=settings, llm_provider=ScriptedProvider([]))

    tool = app.tools.resolve("research_partner")
    assert tool is not None
    assert tool.definition.required_scope == "research:run"
    assert not tool.definition.side_effect   # agent thấy trực tiếp, không qua propose_
    assert "research:run" in app.tools.scopes()
    assert any(d.name == "research_partner" for d in app.tools.agent_definitions())
```

- [x] **Step 5.2: Chạy test xác nhận FAIL đúng lý do**

Run: `uv run --extra dev --extra gmail python -m pytest -q evals/deterministic/test_research_tool.py::test_application_registers_research_partner_tool`
Expected: FAIL với `assert None is not None` (tool chưa được đăng ký)

- [x] **Step 5.3: Implement — `pu/app.py`**

Thêm import ở đầu file (cạnh các import `pu.*` khác, sau `from pu.loop.agent import ...`):

```python
from pu.tools.research import make_tool as make_research_tool
```

Cuối `__init__` của class `PU` (sau dòng `self.report_repository = dependencies.report_repository`), thêm:

```python
        # Chat-triggered research: the registry is built before the
        # orchestrator exists (the orchestrator consumes tool subsets), so
        # the research tool is attached here with a lazy closure over it.
        # run_research_workflow already fans events out to BOTH the chat
        # observer (live streaming) and the durable AgentOps publisher.
        self.tools.register(
            make_research_tool(
                lambda prompt, observer=None: self.run_research_workflow(
                    prompt, observer=observer
                ),
                self.settings.home,
            )
        )
```

- [x] **Step 5.4: Chạy test xác nhận PASS**

Run: `uv run --extra dev --extra gmail python -m pytest -q evals/deterministic/test_research_tool.py`
Expected: `6 passed`

---

### Task 6: Full suite + hygiene

- [x] **Step 6.1: Chạy toàn bộ deterministic suite (kèm extra pdf)**

Run: `uv run --extra dev --extra gmail --extra pdf python -m pytest -q evals/deterministic`
Expected: tất cả pass (baseline trước thay đổi: 409 passed, 13 skipped; sau thay đổi thêm ~13 test mới của 2 file mới)

- [x] **Step 6.2: Ruff trên file mới/sửa**

Run: `uv run --extra dev ruff check pu/reporting/render.py pu/tools/research.py pu/reporting/repository.py pu/app.py evals/deterministic/test_report_rendering.py evals/deterministic/test_research_tool.py`
Expected: không có lỗi mới phát sinh từ các file này (repo có lỗi ruff pre-existing ở file khác — ngoài phạm vi)

- [x] **Step 6.3: Smoke thủ công (user chạy)**

User restart dashboard/terminal chat, rồi hỏi: *"hôm nay có email mới nào không?"* → agent đọc inbox → tự nhận diện ProtonX → gọi `research_partner` → dashboard stream stages → trả tóm tắt + verdict + path `.pu/reports/report-*.pdf`. Mở file PDF kiểm tra dấu tiếng Việt hiển thị đúng.
