"""Human edits create a new report; changed factual text must retain citations."""

from __future__ import annotations

import re
import uuid
from dataclasses import fields

from pu.reporting.evidence import StructuredReportBuilder
from pu.reporting.models import ReportClaim, ReportContradiction, ReportSource, StructuredReport


def revise_report(original: dict, changes: dict) -> StructuredReport:
    if not isinstance(changes, dict) or set(changes) - {"title", "executive_summary", "sections"}:
        raise ValueError("Chỉ sửa title, executive_summary và sections.")
    title = changes.get("title", original["title"])
    summary = changes.get("executive_summary", original["executive_summary"])
    sections = {**original.get("sections", {}), **changes.get("sections", {})}
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError("Tiêu đề phải có 1–300 ký tự.")
    if not isinstance(summary, str) or len(summary) > 15_000:
        raise ValueError("Tóm tắt tối đa 15.000 ký tự.")
    if (
        any(
            not isinstance(k, str) or not isinstance(v, str) or len(v) > 25_000
            for k, v in sections.items()
        )
        or len(sections) > 20
    ):
        raise ValueError("Mỗi mục tối đa 25.000 ký tự, tối đa 20 mục.")
    source_fields = {f.name for f in fields(ReportSource)}
    sources = [
        ReportSource(**{"stage": "workspace", **{k: v for k, v in s.items() if k in source_fields}})
        for s in original.get("sources", [])
    ]
    valid_ids = {s.id for s in sources}
    changed = {k for k, v in sections.items() if v != original.get("sections", {}).get(k)}
    if summary != original["executive_summary"]:
        changed.add("executive_summary")
    claims = [
        ReportClaim(
            **{
                **c,
                "id": c.get("id") or "claim-" + uuid.uuid4().hex,
                "source_ids": tuple(c.get("source_ids", [])),
                "invalid_source_ids": tuple(c.get("invalid_source_ids", [])),
            }
        )
        for c in original.get("claims", [])
        if c.get("section") not in changed
    ]
    for key in sorted(changed):
        text = summary if key == "executive_summary" else sections[key]
        for part in re.split(r"\n\s*\n", text):
            if not part.strip():
                continue
            refs = tuple(dict.fromkeys(re.findall(r"\[([A-Za-z][A-Za-z0-9_-]*)\]", part)))
            claims.append(
                ReportClaim(
                    id="edit-" + uuid.uuid4().hex,
                    text=part.strip(),
                    section=key,
                    source_ids=tuple(s for s in refs if s in valid_ids),
                    invalid_source_ids=tuple(s for s in refs if s not in valid_ids),
                )
            )
    report = StructuredReport(
        report_id="report-" + uuid.uuid4().hex,
        workflow_id=original["workflow_id"],
        schema_version=int(original.get("schema_version", 1)),
        title=title.strip(),
        executive_summary=summary,
        sections=sections,
        claims=claims,
        sources=sources,
        missing_information=list(original.get("missing_information", [])),
        contradictions=[
            ReportContradiction(
                description=c["description"], source_ids=tuple(c.get("source_ids", []))
            )
            for c in original.get("contradictions", [])
        ],
        metadata={
            **original.get("metadata", {}),
            "previous_report_id": original["report_id"],
            "human_edited": True,
        },
    )
    report.markdown = StructuredReportBuilder.render_markdown(report)
    return report
