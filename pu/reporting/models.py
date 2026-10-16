"""Structured report domain models.

Sources are produced by deterministic evidence normalization, not invented by
an LLM. Claims may only reference source ids present in the evidence bundle.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# Versioned separately from the persistence/event schema.  This version is
# recorded on every newly assembled report so a report can be evaluated again
# with the same completeness policy after the policy evolves.
REPORT_SCHEMA_VERSION = 2
REPORT_QUALITY_POLICY_VERSION = "report-quality-v2"
INFORMATION_FIELDS = (
    "website",
    "sector",
    "products",
    "scale",
    "markets",
    "digital_context",
    "contact",
    "request",
    "meeting_preparation",
    "calendar",
)
INFORMATION_STATUSES = {"supported", "not_found", "conflicting", "not_applicable"}


def default_information_coverage() -> list[dict[str, Any]]:
    """Return a conservative checklist for a newly created report.

    An absent/unknown field is explicitly ``not_found``.  This keeps a report
    from gaining completeness merely because its generator omitted a field.
    ``supported`` is only assigned by the generation payload normalizer after
    it has checked references against the evidence bundle.
    """

    return [
        {
            "field": field_name,
            "status": "not_found",
            "source_ids": [],
            "summary": "Chưa tìm thấy dữ kiện đủ tin cậy.",
        }
        for field_name in INFORMATION_FIELDS
    ]


@dataclass(frozen=True)
class ReportSource:
    id: str
    stage: str
    source_type: str
    title: str
    excerpt: str
    tool_name: str | None = None
    url: str | None = None
    domain: str | None = None
    published_at: str | None = None
    observed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    external: bool = False
    derived: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportClaim:
    id: str
    text: str
    section: str
    source_ids: tuple[str, ...] = ()
    invalid_source_ids: tuple[str, ...] = ()
    confidence: float | None = None
    importance: str = "normal"

    @property
    def supported(self) -> bool:
        return bool(self.source_ids) and not self.invalid_source_ids


@dataclass(frozen=True)
class ReportContradiction:
    description: str
    source_ids: tuple[str, ...] = ()


@dataclass
class StructuredReport:
    report_id: str
    workflow_id: str
    title: str
    executive_summary: str
    sections: dict[str, str]
    claims: list[ReportClaim]
    sources: list[ReportSource]
    missing_information: list[str] = field(default_factory=list)
    contradictions: list[ReportContradiction] = field(default_factory=list)
    markdown: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # Reports assembled by StructuredReportBuilder opt in to v2 explicitly.
    # A plain dataclass instance remains a legacy v1 object so loading old
    # snapshots never silently upgrades their quality policy.
    schema_version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
