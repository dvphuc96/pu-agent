"""Human approval application service.

Only authenticated UI/API code may call approve/reject/edit. Agent code only
calls ``propose`` through ToolHarness and never receives a write capability.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pu.approval.models import ApprovalStatus, ProposedAction
if TYPE_CHECKING:
    from pu.core.interfaces.approval import ApprovalRepository
from pu.core.interfaces.tools import ToolContext, ToolDefinition
from pu.harness.audit import action_hash


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def risk_for(tool: ToolDefinition) -> str:
    if tool.external and tool.side_effect:
        return "high"
    if tool.side_effect:
        return "medium"
    return "low"


class HumanApprovalService:
    def __init__(self, repository: "ApprovalRepository", *, ttl_minutes: int = 60):
        self.repository = repository
        self.ttl_minutes = max(1, int(ttl_minutes))

    def _expiry(self) -> str:
        return _iso(datetime.now(UTC) + timedelta(minutes=self.ttl_minutes))

    def propose(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ProposedAction:
        now = _iso(datetime.now(UTC))
        action = ProposedAction(
            id=str(uuid.uuid4()),
            tool_name=tool.name,
            arguments=dict(arguments),
            action_hash=action_hash(tool.name, arguments),
            status=ApprovalStatus.PENDING,
            principal=context.principal or "unknown",
            agent_id=context.agent_id,
            request_id=context.request_id,
            workflow_id=context.workflow_id,
            tool_call_id=context.tool_call_id,
            capability=tool.capability,
            required_scope=tool.required_scope,
            side_effect=tool.side_effect,
            external=tool.external,
            risk=risk_for(tool),
            created_at=now,
            updated_at=now,
            expires_at=self._expiry(),
            metadata={"requested_by_actor": context.actor},
        )
        return self.repository.create(action)

    def get(self, action_id: str) -> ProposedAction | None:
        action = self.repository.get(action_id)
        if action and action.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            now = _iso(datetime.now(UTC))
            if action.expires_at <= now:
                return self.repository.mark_expired(action_id)
        return action

    def list(self, *, limit: int = 100, status: ApprovalStatus | None = None) -> list[ProposedAction]:
        actions = self.repository.list(limit=limit, status=status)
        out = []
        for action in actions:
            refreshed = self.get(action.id)
            if refreshed is not None:
                out.append(refreshed)
        return out

    def edit(self, action_id: str, arguments: dict[str, Any]) -> ProposedAction:
        action = self.get(action_id)
        if action is None:
            raise KeyError("approval not found")
        if action.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            raise ValueError(f"cannot edit approval in status '{action.status.value}'")
        updated = self.repository.edit_pending(
            action_id,
            arguments,
            action_hash(action.tool_name, arguments),
            expires_at=self._expiry(),
        )
        if updated is None:
            raise ValueError("approval changed concurrently; reload and try again")
        return updated

    def approve(self, action_id: str, *, note: str = "", decided_by: str = "local-human") -> ProposedAction:
        action = self.get(action_id)
        if action is None:
            raise KeyError("approval not found")
        if action.status is ApprovalStatus.EXPIRED:
            raise ValueError("approval expired")
        if action.status is not ApprovalStatus.PENDING:
            raise ValueError(f"approval is '{action.status.value}', not pending")
        approved = self.repository.approve(
            action_id, expected_hash=action.action_hash, note=note, decided_by=decided_by
        )
        if approved is None:
            raise ValueError("approval changed or expired; reload and review again")
        return approved

    def reject(self, action_id: str, *, note: str = "", decided_by: str = "local-human") -> ProposedAction:
        action = self.get(action_id)
        if action is None:
            raise KeyError("approval not found")
        if action.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            raise ValueError(f"approval is '{action.status.value}' and cannot be rejected")
        rejected = self.repository.reject(action_id, note=note, decided_by=decided_by)
        if rejected is None:
            raise ValueError("approval changed concurrently; reload and try again")
        return rejected
