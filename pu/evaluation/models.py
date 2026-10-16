"""Evaluation domain models for report quality gates."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeterministicMetrics:
    completeness: float
    source_coverage: float
    source_diversity: float
    freshness: float
    unsupported_claims: int
    invalid_source_refs: int
    contradictions: int
    source_count: int
    unique_domains: int
    official_or_internal_sources: int
    freshness_known_sources: int
    total_claims: int
    overall: float
    # v2 quality metrics.  Defaults deliberately keep the wire format and
    # callers of the v1 evaluator backwards compatible.
    weighted_field_coverage: float = 0.0
    supported_fields: int = 0
    total_fields: int = 0
    public_source_count: int = 0
    public_cited_source_count: int = 0
    public_publisher_count: int = 0
    public_news_freshness: float = 0.0
    public_news_known_sources: int = 0
    public_news_relevant_sources: int = 0
    public_news_future_sources: int = 0
    meeting_readiness: float = 0.0
    duplication_rate: float = 0.0
    duplicate_claims: int = 0
    status_details: dict[str, Any] = field(default_factory=dict)
    rubric_version: str = "legacy"


@dataclass(frozen=True)
class JudgeScore:
    score: float
    reason: str
    criteria: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    # Optional, bounded detail from the judge.  Deterministic gates remain
    # authoritative when these are absent.
    claim_verdicts: list[dict[str, Any]] = field(default_factory=list)
    field_verdicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReportEvaluation:
    report_id: str
    gate_status: str
    release_allowed: bool
    metrics: DeterministicMetrics
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    judge: JudgeScore | None = None
    schema_version: int = 1
    evaluated: bool = True
    quality_policy_version: str = "legacy"
    rubric_version: str = "legacy"
    status_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
