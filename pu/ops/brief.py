"""`python -m pu brief` — deterministic multi-agent research brief."""

from __future__ import annotations

from datetime import date
from rich.console import Console
from pu.core.container import create_application

PROMPT = (
    "Prepare my morning company research brief: inspect recent inbox context, "
    "identify the most relevant company/contact, check internal company context, "
    "public web research, calendar context, and relevant memory."
)


def main() -> None:
    console = Console()
    pu = create_application()
    result = pu.run_research_workflow(PROMPT)
    report = result.report.reply if result.report is not None else "No report was generated."
    console.print(report)
    # This is an internal report draft artifact, not an external delivery/write
    # capability. Sending/publishing remains behind Human Approval.
    out = pu.settings.home / "outbox" / f"research-brief-{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    console.print(f"[dim]draft saved to {out} · workflow {result.workflow_id}[/dim]")


if __name__ == "__main__":
    main()
