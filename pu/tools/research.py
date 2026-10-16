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

# The chat IS the report: the tool returns the full body so the model can
# present it inline in Vietnamese. A hard cap keeps one pathological report
# from blowing the answer budget (the max_tokens lesson) — the saved file
# always holds everything.
_MAX_REPORT_CHARS = 6000

# Canonical workflow section ids → Vietnamese headings for the chat relay.
_SECTION_LABELS_VN = {
    "company": "Giới thiệu công ty",
    "contacts": "Liên hệ & danh tính",
    "email_need": "Nhu cầu từ email",
    "meeting_context": "Bối cảnh cuộc họp",
    "public_research": "Nghiên cứu công khai",
    "recent_updates": "Tin tức mới",
    "risks_opportunities": "Rủi ro & cơ hội",
}


def _report_markdown_vn(report, verdict: str) -> str:
    """Full report body, Vietnamese headings, chat-ready markdown."""
    lines = [
        f"## Nghiên cứu: {report.title}",
        f"Đánh giá chất lượng: {verdict}",
        "",
        "### Tóm tắt",
        (report.executive_summary or "").strip() or "_Không đủ bằng chứng._",
        "",
    ]
    for name, body in report.sections.items():
        lines += [f"### {_SECTION_LABELS_VN.get(name, name)}", body, ""]
    if report.missing_information:
        lines += ["### Thông tin còn thiếu"]
        lines += [f"- {item}" for item in report.missing_information]
        lines.append("")
    slots = getattr(report, "metadata", {}).get("calendar_free_slots") or []
    if slots:
        lines += ["### Lịch trống đề xuất (khung giờ rảnh)"]
        lines += [
            f"- {item.get('label', '')}: {', '.join(item.get('windows') or [])}"
            for item in slots[:3]
        ]
        lines.append("")
    if report.sources:
        lines += ["### Nguồn"]
        lines += [
            f"- {src.id} · {src.source_type} · {src.domain or src.url or src.title}"
            for src in report.sources
        ]
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > _MAX_REPORT_CHARS:
        text = text[:_MAX_REPORT_CHARS].rsplit(" ", 1)[0] + "…"
    return text


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
        report_root = home / "reports"
        candidates = [
            report_root / f"{report.report_id}.{ext}" for ext in ("pdf", "html", "md")
        ]
        readable = next((str(p) for p in candidates if p.is_file()), str(candidates[-1]))
        slot_line = ""
        slots = getattr(report, "metadata", {}).get("calendar_free_slots") or []
        if slots:
            slot_line = (
                "When meeting timing comes up, suggest one of the listed free "
                "slots on the user's calendar.\n"
            )
        return (
            f"{_report_markdown_vn(report, verdict)}\n\n"
            f"{slot_line}"
            f"Archive copy: {readable}\n"
            f"Workflow id: {workflow_id}\n"
            "Relay: present the report above to the user inline in Vietnamese, "
            "in full, as your chat reply — do not shorten it to a summary and "
            "do not point the user at the file (mention the archive copy in "
            "one line only). Then offer the approval choice: forward the "
            "report by email or save it to knowledge — do neither without "
            "approval."
        )

    return Tool(
        name="research_partner",
        description=(
            "Run the full multi-agent research workflow (web, company info, "
            "calendar, memory, report generation, evaluation) for ONE company. "
            "Use it right after reading the inbox when you find email from a "
            "new partner or customer that no research brief exists for yet — "
            "call it in the SAME turn you discover the email; do not stop at "
            "merely suggesting the user review it. "
            "Pass the company name plus website/sender taken from the email. "
            "It takes minutes and streams progress events; when it returns, "
            "present the FULL report inline in Vietnamese as your chat reply "
            "(the tool output already carries it), mention the saved file as "
            "an archive copy in one line only, and always offer the approval "
            "choice (forward by email / save to knowledge) before taking any "
            "action."
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
                    "description": "website URL found in the email signature — "
                    "pass it whenever the signature contains one; it anchors "
                    "research to the right company domain (lookalike domains "
                    "poison the brief)",
                },
                "sender_email": {
                    "type": "string",
                    "description": "sender email address from the email",
                },
                "context": {
                    "type": "string",
                    "description": "1-2 sentences: why this partner matters or "
                    "what the email asks for — include any dates, deadlines, or "
                    "meeting times the email mentions",
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
