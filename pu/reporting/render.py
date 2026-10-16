"""Readable report rendering: structured report → HTML → PDF.

Two layouts, one content model:

- ``render_html`` — the NotebookLM-style browser view: a left "Nguồn" panel
  with source cards, a right document pane, and claims rendered as lines with
  numbered citation chips that anchor to the source cards.
- ``render_print_html`` — a flat single-column document for xhtml2pdf, which
  has no grid/flex support; sources become the provenance table at the end.

Every field of a report originates from untrusted web/email evidence, so all
content is HTML-escaped before it reaches the template.
"""

from __future__ import annotations

import html
import inspect
import re
from pathlib import Path
from typing import Any

from pu.reporting.citations import (
    build_citation_presentation,
    canonical_public_url,
    collect_citation_ids,
)
from pu.reporting.models import StructuredReport

# xhtml2pdf is an optional extra (`uv sync --extra pdf`); without it we still
# write .html and skip the .pdf instead of failing the report pipeline.
try:
    from xhtml2pdf import pisa
except ImportError:  # pragma: no cover - depends on installed extras
    pisa = None

# Helvetica's built-in PDF fonts are Latin-1 only — Vietnamese diacritics
# render as black boxes. Any Unicode TTF here fixes that; macOS ships Arial
# Unicode, Linux distros usually have DejaVu or Liberation. Browsers never
# load it (they resolve the system stack first), only xhtml2pdf falls through
# to 'Viet'.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

# NotebookLM-ish icon: a lettered colored square per source type. Colors are
# our literals (never untrusted data), so they are safe to inline as styles.
_SOURCE_STYLES = {
    "web": ("W", "#f29900"),
    "email": ("@", "#1a73e8"),
    "calendar": ("C", "#188038"),
    "memory": ("M", "#9334e6"),
    "internal_knowledge": ("K", "#12b5cb"),
    "agent_output": ("A", "#5f6368"),
}
_DEFAULT_SOURCE_STYLE = ("•", "#5f6368")

# Canonical workflow section ids → human headings (markdown uses its own map
# in evidence.py; render fallback keeps any custom section name as-is).
_SECTION_LABELS = {
    "company": "Company Overview",
    "contacts": "Contacts & Relationship",
    "email_need": "Email / Expressed Need",
    "meeting_context": "Meeting Context",
    "public_research": "Public Research",
    "recent_updates": "Recent Updates",
    "risks_opportunities": "Risks / Opportunities",
    "company_scale": "Scale & Operations",
    "business_context": "Business & Technology Context",
    "meeting_preparation": "Meeting Preparation",
}

_BROWSER_CSS = """
body { background:#f0f4f9; color:#1f1f1f; margin:0; padding:28px 20px;
  font-family:-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;
  font-size:15px; line-height:1.65; }
.app { max-width:1150px; margin:0 auto; display:grid;
  grid-template-columns:300px minmax(0,1fr); gap:20px; align-items:start; }
.sources-panel { background:#ffffff; border:1px solid #dde3ea; border-radius:16px;
  padding:16px; position:sticky; top:20px; }
.panel-title { font-size:15px; font-weight:600; margin:2px 4px 12px; }
.src-card { display:flex; gap:10px; padding:10px 8px; border-radius:10px;
  align-items:flex-start; }
.src-card:hover { background:#f5f8fd; }
.src-icon { flex:0 0 34px; width:34px; height:34px; border-radius:9px;
  color:#ffffff; font-size:13px; font-weight:700; text-align:center; line-height:34px; }
.src-title { font-size:13.5px; font-weight:600; margin-bottom:2px; }
.src-title a { color:#1f1f1f; text-decoration:none; }
.src-snippet { font-size:12px; color:#5f6368; overflow:hidden; max-height:34px; }
.src-count { margin-top:12px; padding:8px; border-top:1px solid #eceff4;
  font-size:12.5px; color:#5f6368; }
.doc { background:#ffffff; border:1px solid #dde3ea; border-radius:16px;
  padding:36px 44px; }
.overline { font-size:11px; font-weight:700; letter-spacing:1.4px;
  text-transform:uppercase; color:#5f6368; }
h1 { font-size:27px; line-height:1.25; margin:6px 0; }
.meta { color:#5f6368; font-size:13px; margin:0 0 18px; }
.verdict { display:inline-block; border-radius:999px; padding:6px 16px;
  font-size:13.5px; margin:0 8px 6px 0; }
.pass { background:#e6f4ea; color:#137333; }
.fail { background:#fce8e6; color:#c5221f; }
.stat { display:inline-block; background:#f1f3f4; border-radius:8px;
  padding:5px 12px; margin:0 6px 6px 0; font-size:12.5px; color:#3c4043; }
.stat b { color:#1f1f1f; }
h2 { font-size:17.5px; margin:26px 0 8px; }
.claims { margin-top:10px; }
.claim { font-size:14px; padding:6px 10px; border-radius:8px;
  background:#f8f9fa; margin:6px 0; }
.cite { display:inline-block; min-width:18px; padding:0 5px; height:18px;
  line-height:18px; border-radius:5px; color:#ffffff; font-size:11px;
  font-weight:700; text-align:center; text-decoration:none; margin-left:5px;
  vertical-align:2px; }
.slots { margin-top:26px; border:1px solid #dde3ea; border-radius:12px;
  padding:16px 18px; background:#f8fbff; }
.slots-title { font-weight:700; margin-bottom:8px; }
.availability-meta { color:#5f6368; font-size:12.5px; margin-bottom:8px; }
.slot-chip { display:inline-block; background:#e8f0fe; color:#174ea6;
  border-radius:6px; padding:2px 8px; margin:2px 4px 2px 0; font-size:12.5px; }
.warn { margin-top:26px; background:#fef7e0; border:1px solid #f0d08a;
  border-radius:12px; padding:16px 18px; }
.warn h2 { color:#b06000; margin-top:0; }
.coverage { margin-top:26px; border:1px solid #dde3ea; border-radius:12px;
  padding:14px 18px; background:#fbfcfe; }
.coverage h2 { margin-top:0; }
.coverage ul { margin:4px 0 0 18px; padding:0; }
.foot { text-align:center; color:#9aa0a6; font-size:12px; margin-top:18px; }
@media (max-width: 860px) {
  .app { grid-template-columns:1fr; }
  .sources-panel { position:static; }
  .doc { padding:24px 20px; }
}
"""

_PRINT_CSS = """
@page { size: a4; margin: 15mm 14mm; }
body { background:#ffffff; color:#1f1f1f; font-size:11pt; line-height:1.6;
  font-family:-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,'Viet',sans-serif;
  margin:0; padding:12pt; }
.page { width:100%; }
.card { border:1pt solid #e6e0d5; border-radius:10pt; padding:14pt 16pt;
  margin:0 0 12pt 0; }
.overline { font-size:9pt; font-weight:600; letter-spacing:1.2pt;
  text-transform:uppercase; color:#5f6368; margin:0 0 6pt 0; }
h1 { font-size:19pt; line-height:1.25; margin:0 0 6pt 0; }
h2 { font-size:13pt; margin:12pt 0 6pt 0; }
.meta { color:#5f6368; font-size:10pt; margin:0; }
.verdict { display:inline-block; border-radius:999pt; padding:5pt 12pt;
  font-size:10.5pt; margin:0 6pt 5pt 0; }
.pass { background:#e6f4ea; color:#137333; }
.fail { background:#fce8e6; color:#c5221f; }
.stat { display:inline-block; background:#f1f3f4; border-radius:6pt;
  padding:4pt 9pt; margin:0 5pt 5pt 0; font-size:9.5pt; color:#3c4043; }
.stat b { color:#1f1f1f; }
.claims { margin-top:6pt; }
.claim { font-size:10pt; padding:5pt 8pt; border-radius:6pt;
  background:#f8f9fa; margin:5pt 0; }
.cite { display:inline-block; background:#1a73e8; color:#ffffff;
  border-radius:3pt; padding:1pt 5pt; margin-left:4pt; font-size:8pt;
  font-weight:700; text-decoration:none; }
.slots { border:1pt solid #ccd7e5; border-radius:8pt; padding:10pt 12pt;
  background:#f8fbff; margin:10pt 0; }
.slots-title { font-weight:700; margin-bottom:5pt; }
.availability-meta { color:#5f6368; font-size:9pt; margin-bottom:5pt; }
.slot-chip { display:inline-block; background:#e8f0fe; color:#174ea6;
  border-radius:4pt; padding:1.5pt 6pt; margin:1.5pt 3pt 1.5pt 0; font-size:9pt; }
.warn { background:#fef7e0; border:1pt solid #f0d08a; border-radius:8pt;
  padding:10pt 12pt; margin:10pt 0; }
.warn h2 { color:#b06000; margin-top:0; }
.coverage { border:1pt solid #dde3ea; border-radius:8pt; padding:10pt 12pt; margin:10pt 0; }
.coverage h2 { margin-top:0; }
table { border-collapse:collapse; width:100%; font-size:9.5pt; }
th, td { border-bottom:0.5pt solid #ececec; padding:6pt 5pt; text-align:left;
  vertical-align:top; }
th { color:#5f6368; font-size:8.5pt; text-transform:uppercase; letter-spacing:0.8pt; }
ul { margin:4pt 0 0 0; }
a { color:#1a73e8; text-decoration:none; }
.foot { text-align:center; color:#9aa0a6; font-size:9pt; margin-top:6pt; }
"""


def _font_face() -> str:
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return f"@font-face {{ font-family: 'Viet'; src: url('{path.as_uri()}'); }}"
    return ""


def _cited_text(text: str, presentation: dict[str, dict[str, Any]]) -> str:
    """Escape prose and turn technical ``[S1]``/``[W1]`` refs into [1] chips."""

    escaped = html.escape(text or "")
    pattern = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*)\]")

    def replace(match: re.Match[str]) -> str:
        source_id = match.group(1)
        item = presentation.get(source_id)
        if not item:
            return "[Nguồn chưa xác minh]"
        label = str(item.get("label") or "Nguồn chưa xác minh")
        if item.get("number") is not None:
            label = f"{item['number']}"
        rendered = f"[{html.escape(label)}]"
        if item.get("clickable") and item.get("anchor"):
            return (
                f" <a class='cite inline-cite' href='#{html.escape(str(item['anchor']))}' "
                f"aria-label='Nguồn {html.escape(label)}'>{rendered}</a>"
            )
        return f" <span class='cite inline-cite cite-internal'>{rendered}</span>"

    return pattern.sub(replace, escaped).replace("\n", "<br/>")


def _section_label(name: str) -> str:
    return _SECTION_LABELS.get(name, name)


def _snippet(source: Any) -> str:
    safe_url = canonical_public_url(getattr(source, "url", None))
    return (source.excerpt or "").strip() or (safe_url or source.domain or "")


def _display_sources(report: StructuredReport) -> list[Any]:
    """Keep the reader's public source list compact while retaining cited links."""

    cited = collect_citation_ids(report)
    public_count = 0
    seen_public: set[str] = set()
    selected: list[Any] = []
    for source in report.sources:
        if source.derived:
            continue
        is_public = bool(source.external and source.url and not source.derived)
        if is_public:
            canonical = canonical_public_url(source.url)
            if canonical and canonical in seen_public:
                continue
            if canonical:
                seen_public.add(canonical)
            public_count += 1
            if public_count > 8 and source.id not in cited:
                continue
        selected.append(source)
    return selected


def _coverage_html(report: StructuredReport, evaluation: Any | None = None) -> str:
    coverage = report.metadata.get("information_coverage") if isinstance(report.metadata, dict) else None
    if not isinstance(coverage, list) or not coverage:
        return ""
    field_labels = {
        "website": "Website", "sector": "Lĩnh vực", "products": "Sản phẩm/dịch vụ",
        "scale": "Quy mô", "markets": "Thị trường", "digital_context": "Bối cảnh số",
        "contact": "Người liên hệ", "request": "Nhu cầu từ email",
        "meeting_preparation": "Chuẩn bị cuộc họp", "calendar": "Lịch",
    }
    labels = {"supported": "Đã có nguồn", "not_found": "Chưa tìm thấy", "conflicting": "Nguồn mâu thuẫn", "not_applicable": "Ngoài phạm vi"}
    evaluated_details: dict[str, Any] = {}
    if evaluation is not None:
        metrics = evaluation.get("metrics") if isinstance(evaluation, dict) else getattr(evaluation, "metrics", None)
        details = metrics.get("status_details") if isinstance(metrics, dict) else getattr(metrics, "status_details", None)
        if isinstance(details, dict):
            evaluated_details = details.get("field_coverage") or {}
    parts = ["<div class='coverage'><h2>Phạm vi thông tin đã kiểm tra</h2><ul>"]
    for item in coverage:
        if not isinstance(item, dict):
            continue
        status = labels.get(str(item.get("status")), "Chưa đánh giá")
        field_key = str(item.get("field") or "")
        evaluated = evaluated_details.get(field_key)
        if isinstance(evaluated, dict):
            if evaluated.get("supported"):
                status = "Đã đối chiếu với nguồn"
            elif str(item.get("status")) == "supported":
                status = "Chưa đối chiếu được"
        elif str(item.get("status")) == "supported":
            status = "Theo checklist, chưa đối chiếu"
        detail = str(item.get("summary") or item.get("reason") or "")
        extra = f" — {html.escape(detail)}" if detail else ""
        field = field_labels.get(field_key, field_key)
        parts.append(f"<li><b>{html.escape(field)}</b>: {status}{extra}</li>")
    parts.append("</ul></div>")
    return "".join(parts)


def _slots(report: StructuredReport) -> list[dict]:
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    slots = metadata.get("calendar_free_slots") or []
    return [item for item in slots if isinstance(item, dict) and item.get("windows")]


def _slots_html(report: StructuredReport, label_class: str, chip_class: str) -> str:
    slots = _slots(report)
    if not slots:
        return ""
    esc = html.escape
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    availability = metadata.get("availability") if isinstance(metadata.get("availability"), dict) else {}
    checked_at = availability.get("checked_at") or metadata.get("calendar_checked_at")
    timezone = availability.get("timezone") or metadata.get("calendar_timezone") or "chưa xác định"
    scope = availability.get("scope") or metadata.get("calendar_scope") or "lịch local; chưa biết lịch đối tác"
    checked_sources = availability.get("checked_sources") or metadata.get("calendar_checked_sources") or []
    source_note = ", ".join(str(value) for value in checked_sources if value) or "chưa xác định nguồn lịch"
    snapshot = (
        f"Đã kiểm tra lúc {html.escape(str(checked_at)) if checked_at else 'chưa lưu thời điểm kiểm tra'}"
        f" · Múi giờ: {html.escape(str(timezone))} · Phạm vi: {html.escape(str(scope))}"
        f" · Nguồn: {html.escape(source_note)}"
    )
    parts = [
        f"<div class='slots'><div class='{label_class}'>Lịch trống đề xuất (khung giờ rảnh)</div>",
        f"<div class='availability-meta'>{snapshot}</div>",
    ]
    for item in slots:
        chips = "".join(
            f"<span class='{chip_class}'>{esc(str(w))}</span>"
            for w in item.get("windows", [])
        )
        parts.append(f"<div><b>{esc(str(item.get('label') or item.get('date') or ''))}</b> {chips}</div>")
    parts.append("</div>")
    return "".join(parts)


class ReportRenderer:
    """Deterministic structured-report → HTML/PDF renderer (no LLM)."""

    def render_html(self, report: StructuredReport, evaluation: Any | None = None) -> str:
        esc = html.escape
        type_of = {s.id: s.source_type for s in report.sources}
        presentation = build_citation_presentation(report)
        display_sources = _display_sources(report)
        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<link rel='icon' href='data:,'/>",   # no favicon 404 noise on file://
            f"<title>{esc(report.title)}</title>",
            f"<style>{_BROWSER_CSS}</style></head><body><div class='app'>",
            "<aside class='sources-panel'>",
            "<div class='panel-title'>Nguồn</div>",
        ]
        for src in display_sources:
            letter, color = _SOURCE_STYLES.get(src.source_type, _DEFAULT_SOURCE_STYLE)
            title = esc(src.title)
            source_presentation = presentation.get(src.id) or {}
            if source_presentation.get("clickable") and source_presentation.get("url"):
                title = f"<a href='{esc(str(source_presentation['url']))}'>{title}</a>"
            parts.append(
                f"<div class='src-card' id='src-{esc(src.id)}'>"
                f"<span class='src-icon' style='background:{color}'>{letter}</span>"
                f"<div><div class='src-title'>{title}</div>"
                f"<div class='src-snippet'>{esc(_snippet(src)[:170])}</div></div></div>"
            )
        parts.append(f"<div class='src-count'>Số nguồn: {len(display_sources)}</div></aside>")

        parts.append("<main class='doc'>")
        parts.append("<div class='overline'>Partner research brief</div>")
        parts.append(f"<h1>{esc(report.title)}</h1>")
        parts.append(
            "<p class='meta'>Workflow " + esc(report.workflow_id)
            + " · generated " + esc(report.generated_at)
            + f" · {len(report.sources)} sources</p>"
        )
        if evaluation is not None:
            parts.append(self._verdict_html(evaluation))
            parts.append(self._stats_html(evaluation))
        parts.append("<h2>Executive Summary</h2>")
        parts.append(f"<p>{_cited_text(report.executive_summary, presentation)}</p>")
        for name, body in report.sections.items():
            parts.append(f"<h2>{esc(_section_label(name))}</h2>")
            parts.append(f"<p>{_cited_text(body, presentation)}</p>")
            claims = [c for c in (report.claims or []) if c.section == name]
            if claims:
                parts.append("<div class='claims'>")
                for claim in claims:
                    chips = "".join(self._cite_chip(sid, type_of, presentation) for sid in claim.source_ids)
                    parts.append(f"<div class='claim'>{esc(claim.text)}{chips}</div>")
                parts.append("</div>")
        parts.append(_slots_html(report, "slots-title", "slot-chip"))
        if report.missing_information:
            parts.append("<div class='warn'><h2>Missing Information</h2><ul>")
            parts.extend(f"<li>{_cited_text(item, presentation)}</li>" for item in report.missing_information)
            parts.append("</ul></div>")
        parts.append(_coverage_html(report, evaluation))
        parts.append(f"<div class='foot'>Generated by PU Agent · {esc(report.report_id)}</div>")
        parts.append("</main></div></body></html>")
        return "".join(parts)

    def render_print_html(self, report: StructuredReport, evaluation: Any | None = None) -> str:
        """Flat single-column variant for xhtml2pdf (no grid/flex support)."""
        esc = html.escape
        type_of = {s.id: s.source_type for s in report.sources}
        presentation = build_citation_presentation(report)
        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>{esc(report.title)}</title>",
            f"<style>{_PRINT_CSS}</style></head><body><div class='page'>",
            "<div class='card'>",
            "<div class='overline'>Partner research brief</div>",
            f"<h1>{esc(report.title)}</h1>",
            "<p class='meta'>Workflow " + esc(report.workflow_id)
            + " · generated " + esc(report.generated_at)
            + f" · {len(report.sources)} sources</p>",
            "</div>",
        ]
        if evaluation is not None:
            parts.append("<div class='card'>")
            parts.append(self._verdict_html(evaluation))
            parts.append(self._stats_html(evaluation))
            parts.append("</div>")
        parts.append("<div class='card'>")
        parts.append("<div class='overline'>Executive Summary</div>")
        parts.append(f"<p>{_cited_text(report.executive_summary, presentation)}</p></div>")
        for name, body in report.sections.items():
            parts.append("<div class='card'>")
            parts.append(f"<h2>{esc(_section_label(name))}</h2>")
            parts.append(f"<p>{_cited_text(body, presentation)}</p>")
            claims = [c for c in (report.claims or []) if c.section == name]
            if claims:
                parts.append("<div class='claims'>")
                for claim in claims:
                    chips = "".join(self._cite_chip(sid, type_of, presentation) for sid in claim.source_ids)
                    parts.append(f"<div class='claim'>{esc(claim.text)}{chips}</div>")
                parts.append("</div>")
            parts.append("</div>")
        slots = _slots_html(report, "slots-title", "slot-chip")
        if slots:
            parts.append("<div class='card'>" + slots + "</div>")
        if report.missing_information:
            parts.append("<div class='card warn'>")
            parts.append("<h2>Missing Information</h2><ul>")
            parts.extend(f"<li>{_cited_text(item, presentation)}</li>" for item in report.missing_information)
            parts.append("</ul></div>")
        parts.append(_coverage_html(report, evaluation))
        if report.sources:
            parts.append("<div class='card'>")
            parts.append("<div class='overline'>Sources / Provenance</div><table>")
            parts.append("<tr><th>ID</th><th>Type</th><th>Source</th><th>Excerpt</th></tr>")
            cited_ids = collect_citation_ids(report)
            seen_public: set[str] = set()
            public_count = 0
            for src in report.sources:
                if src.derived:
                    continue
                is_public = bool(src.external and canonical_public_url(src.url))
                canonical = canonical_public_url(src.url) if is_public else None
                if canonical and canonical in seen_public:
                    continue
                if is_public:
                    public_count += 1
                    if public_count > 8 and src.id not in cited_ids:
                        continue
                    if canonical:
                        seen_public.add(canonical)
                elif src.id not in cited_ids:
                    continue
                excerpt = _snippet(src)[:180]
                if len(_snippet(src)) > 180:
                    excerpt = excerpt.rsplit(" ", 1)[0] + "…"
                source_presentation = presentation.get(src.id) or {}
                if source_presentation.get("clickable") and source_presentation.get("url"):
                    where = f"<a href='{esc(str(source_presentation['url']))}'>{esc(src.domain or source_presentation['url'])}</a>"
                else:
                    where = esc(src.domain or "")
                citation_label = str(source_presentation.get("number") or source_presentation.get("label") or "Nguồn chưa xác minh")
                parts.append(
                    "<tr>"
                    f"<td><a name='src-{esc(src.id)}'></a><span class='cite'>{esc(citation_label)}</span> <small>{esc(src.id)}</small></td>"
                    f"<td>{esc(src.source_type)}</td>"
                    f"<td>{esc(src.title)}<br/>{where}</td>"
                    f"<td>{esc(excerpt)}</td>"
                    "</tr>"
                )
            parts.append("</table></div>")
        parts.append(f"<div class='foot'>Generated by PU Agent · {esc(report.report_id)}</div>")
        parts.append("</div></body></html>")
        return "".join(parts)

    @staticmethod
    def _cite_chip(source_id: str, type_of: dict[str, str], presentation: dict[str, dict[str, Any]]) -> str:
        item = presentation.get(source_id)
        label = str((item or {}).get("number") or (item or {}).get("label") or "Nguồn chưa xác minh")
        _, color = _SOURCE_STYLES.get(type_of.get(source_id, ""), _DEFAULT_SOURCE_STYLE)
        if not item or not item.get("clickable") or not item.get("anchor"):
            return f"<span class='cite cite-internal' style='background:{color}'>{html.escape(label)}</span>"
        return (
            f"<a class='cite' style='background:{color}' "
            f"href='#{html.escape(str(item['anchor']))}'>{html.escape(label)}</a>"
        )

    @staticmethod
    def _stats_html(evaluation: Any) -> str:
        metrics = evaluation.get("metrics") if isinstance(evaluation, dict) else getattr(evaluation, "metrics", None)
        if metrics is None:
            return ""
        parts: list[str] = []
        for label, value in (
            ("Completeness", metrics.get("completeness") if isinstance(metrics, dict) else getattr(metrics, "completeness", None)),
            ("Source coverage", metrics.get("source_coverage") if isinstance(metrics, dict) else getattr(metrics, "source_coverage", None)),
            ("Freshness", metrics.get("freshness") if isinstance(metrics, dict) else getattr(metrics, "freshness", None)),
        ):
            if isinstance(value, (int, float)):
                parts.append(f"<span class='stat'>{label} <b>{value:.0%}</b></span>")
        count = metrics.get("source_count") if isinstance(metrics, dict) else getattr(metrics, "source_count", None)
        if isinstance(count, int):
            domains = metrics.get("unique_domains") if isinstance(metrics, dict) else getattr(metrics, "unique_domains", None)
            extra = f" · {domains} domains" if isinstance(domains, int) else ""
            parts.append(f"<span class='stat'>Sources <b>{count}</b>{extra}</span>")
        return "".join(parts)

    @staticmethod
    def _verdict_html(evaluation: Any) -> str:
        metrics = evaluation.get("metrics") if isinstance(evaluation, dict) else getattr(evaluation, "metrics", None)
        overall = metrics.get("overall") if isinstance(metrics, dict) else getattr(metrics, "overall", None)
        allowed = bool(evaluation.get("release_allowed", False) if isinstance(evaluation, dict) else getattr(evaluation, "release_allowed", False))
        gate = evaluation.get("gate_status", "unknown") if isinstance(evaluation, dict) else getattr(evaluation, "gate_status", "unknown")
        cls = "pass" if allowed else "fail"
        word = "ĐẠT chuẩn chất lượng" if allowed else "CHƯA ĐẠT ngưỡng chất lượng"
        score = f"{overall:.0%}" if isinstance(overall, (int, float)) else "n/a"
        return (
            f"<span class='verdict {cls}'><b>{word}</b> · overall {score}"
            f" · gate: {html.escape(str(gate))}</span>"
        )

    def write_pdf(self, html_text: str, path: Path) -> Path | None:
        """Write a PDF next to the .html report; None means "not possible"
        (library extra not installed, or the converter errored). Never raises:
        a report pipeline must not fail because of presentation."""
        if pisa is None:
            return None
        # The Unicode TTF is a xhtml2pdf-only concern; a browser would try to
        # fetch the file:// font and log an error, so render_html omits it and
        # it is injected here, into the PDF source only.
        pdf_source = html_text.replace("<style>", f"<style>{_font_face()}", 1)
        font = next((Path(p) for p in _FONT_CANDIDATES if Path(p).is_file()), None)
        options = {}
        if font:
            pdf_source = pdf_source.replace(
                "font-family:-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,'Viet',sans-serif;",
                "font-family:'Viet',sans-serif;",
            )
            # Newer xhtml2pdf confines resource reads. Permit only this selected
            # font file, not its entire directory, and disable remote resources.
            if "resource_policy" in inspect.signature(pisa.CreatePDF).parameters:
                try:
                    from xhtml2pdf.config.resources import ResourceAccessPolicy
                    options["resource_policy"] = ResourceAccessPolicy(
                        base_dir=None, extra_roots=(font.resolve(),), allow_remote=False,
                    )
                except ImportError:
                    pass  # Older xhtml2pdf versions do not expose this policy.
        try:
            with open(path, "wb") as dest:
                status = pisa.CreatePDF(src=pdf_source, dest=dest, encoding="utf-8", **options)
        except Exception:
            path.unlink(missing_ok=True)
            return None
        if status.err:
            path.unlink(missing_ok=True)
            return None
        return path
