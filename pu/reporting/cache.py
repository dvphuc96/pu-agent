"""Reuse company evidence separately from short-lived calendar snapshots."""
from __future__ import annotations

from datetime import UTC, datetime

from pu.reporting.policy import COMPANY_EVIDENCE_MAX_AGE_SECONDS, CURRENT_RUBRIC_VERSION


def company_evidence_is_current(payload: dict, *, reference: datetime | None = None) -> bool:
    metadata = payload.get("metadata") or {}
    if (payload.get("evaluation") or {}).get("rubric_version") != CURRENT_RUBRIC_VERSION:
        return False
    try:
        stamp = datetime.fromisoformat(metadata["company_evidence_checked_at"])
        current = reference or datetime.now(UTC)
        return stamp.tzinfo is not None and 0 <= (current - stamp).total_seconds() <= COMPANY_EVIDENCE_MAX_AGE_SECONDS
    except (KeyError, ValueError, TypeError):
        return False


def reusable_company_sources(payload: dict, enabled_source_ids: set[str]) -> list[dict]:
    """Return only the evidence used by this report, respecting source removal."""
    result = []
    for source in payload.get("sources") or []:
        metadata = source.get("metadata") or {}
        original_id = metadata.get("workspace_source_id")
        if (
            source.get("derived")
            or source.get("source_type") not in {"web", "news", "internal_knowledge", "memory", "document"}
            or not original_id or original_id not in enabled_source_ids
            or not source.get("excerpt")
        ):
            continue
        result.append({
            "id": original_id, "title": source["title"], "source_type": source["source_type"],
            "content": source["excerpt"], "url": source.get("url") or "",
            "published_at": source.get("published_at"), "observed_at": source.get("observed_at"),
            "metadata": {**metadata, "evidence_cache_reused": True}, "enabled": True,
        })
    return result
