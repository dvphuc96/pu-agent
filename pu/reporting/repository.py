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
        # The browser gets the two-pane NotebookLM view; the PDF pipeline gets
        # the flat print layout (xhtml2pdf has no grid support).
        html_text = self.renderer.render_html(report, evaluation)
        (self.root / f"{report.report_id}.html").write_text(html_text, encoding="utf-8")
        self.renderer.write_pdf(
            self.renderer.render_print_html(report, evaluation),
            self.root / f"{report.report_id}.pdf",
        )

    def list(self, limit: int = 50) -> list[dict]:
        items = []
        for path in sorted(self.root.glob("report-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append(data)
        return items
