"""Deterministic routing of bounded workspace evidence to research roles.

Bounded workspace research already supplies selected first-order evidence. This
module gives each role a small, source-referenced view of that evidence so the
orchestrator can preserve role/checkpoint/event semantics without spending a
model call on five analyses that are discarded before report generation.
"""

from __future__ import annotations

import json
from typing import Any

ROLE_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "email": ("email",),
    "company": ("email", "internal_knowledge"),
    "web": ("web", "news"),
    "calendar": ("calendar",),
    "memory": ("memory",),
}


def _snippet(source: dict[str, Any], limit: int = 1200) -> str:
    content = source.get("content") or source.get("excerpt") or source.get("snippet") or ""
    return " ".join(str(content).split())[:limit]


def route_evidence(sources: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, dict[str, Any]]:
    """Return stable, bounded role contexts containing only source references.

    The returned values are routing context, not new evidence.  Source IDs are
    copied from the workspace records unchanged so later report provenance can
    retain the original workspace source identity.
    """

    contexts: dict[str, dict[str, Any]] = {}
    for role, allowed_types in ROLE_SOURCE_TYPES.items():
        references = []
        for index, source in enumerate(sources, 1):
            if not isinstance(source, dict) or source.get("source_type") not in allowed_types:
                continue
            source_id = source.get("id") or source.get("source_id")
            if not source_id:
                continue
            references.append(
                {
                    "source_id": str(source_id),
                    "report_source_id": str(source.get("report_source_id") or f"W{index}"),
                    "source_type": str(source.get("source_type")),
                    "title": str(source.get("title") or "")[:300],
                    "snippet": _snippet(source),
                }
            )
        contexts[role] = {
            "role": role,
            "source_ids": [item["source_id"] for item in references],
            "references": references,
            "routing_only": True,
            "note": "Đây là ngữ cảnh định tuyến; nguồn gốc vẫn là workspace source và không phải bằng chứng mới.",
        }
    return contexts


def role_context_text(contexts: dict[str, dict[str, Any]]) -> str:
    """Serialize role contexts for a report generator prompt."""

    return json.dumps(
        {role: contexts[role] for role in ROLE_SOURCE_TYPES if role in contexts},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def role_agent_result(role: str, context: dict[str, Any]):
    """Build a zero-LLM AgentResult preserving the workflow role contract."""

    from pu.core.interfaces.agents import AgentResult

    return AgentResult(
        reply=json.dumps(context, ensure_ascii=False),
        tool_calls=[],
        iterations=0,
        usage={"in": 0, "out": 0, "calls": 0},
    )


__all__ = ["ROLE_SOURCE_TYPES", "role_agent_result", "role_context_text", "route_evidence"]
