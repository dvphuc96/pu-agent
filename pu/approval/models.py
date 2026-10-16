"""Human-approval domain models.

Approval is authorization for one exact action payload, never a blanket grant.
The action hash is recomputed whenever the payload changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset({
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXECUTED,
    ApprovalStatus.FAILED,
    ApprovalStatus.EXPIRED,
})


@dataclass(frozen=True)
class ProposedAction:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    action_hash: str
    status: ApprovalStatus
    principal: str
    agent_id: str | None
    request_id: str | None
    workflow_id: str | None
    tool_call_id: str | None
    capability: str
    required_scope: str
    side_effect: bool
    external: bool
    risk: str
    created_at: str
    updated_at: str
    expires_at: str
    approved_action_hash: str | None = None
    decision_note: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    execution_id: str | None = None
    executed_at: str | None = None
    result_summary: str | None = None
    error: str | None = None
    revision: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
