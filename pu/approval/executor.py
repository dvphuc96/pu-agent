"""One-shot execution of human-approved actions."""

from __future__ import annotations

import uuid

from pu.approval.models import ApprovalStatus, ProposedAction
from pu.approval.service import HumanApprovalService
from pu.core.interfaces.tools import ToolContext, ToolHarness, ToolResult


class ApprovedActionExecutor:
    """The only component granted real write capabilities after human approval."""

    def __init__(self, approvals: HumanApprovalService, tool_harness: ToolHarness):
        self.approvals = approvals
        self.tool_harness = tool_harness

    def execute(self, action_id: str, *, observer=None) -> tuple[ProposedAction, ToolResult]:
        action = self.approvals.get(action_id)
        if action is None:
            raise KeyError("approval not found")
        if action.status is not ApprovalStatus.APPROVED:
            raise ValueError(f"approval is '{action.status.value}', not approved")
        if action.approved_action_hash != action.action_hash:
            raise ValueError("approved payload hash does not match current action")

        execution_id = str(uuid.uuid4())
        claimed = self.approvals.repository.claim_execution(
            action.id,
            expected_hash=action.action_hash,
            execution_id=execution_id,
        )
        if claimed is None:
            raise ValueError("approval already consumed, expired, or changed concurrently")

        notify = observer or (lambda kind, ev: None)
        notify("approval_execution_started", {
            "approval_id": claimed.id, "execution_id": execution_id,
            "workflow_id": claimed.workflow_id, "request_id": claimed.request_id,
            "agent_id": claimed.agent_id, "tool_call_id": claimed.tool_call_id,
            "tool": claimed.tool_name, "action_hash": claimed.action_hash,
        })

        context = ToolContext(
            observer=observer,
            actor="approved_action_executor",
            principal=claimed.principal,
            request_id=claimed.request_id,
            workflow_id=claimed.workflow_id,
            agent_id=claimed.agent_id,
            tool_call_id=claimed.tool_call_id,
            scopes=frozenset({claimed.required_scope}),
            capabilities=frozenset({claimed.capability}),
            metadata={
                **claimed.metadata,
                "approval_id": claimed.id,
                "approval_action_hash": claimed.action_hash,
                "approval_status": "executing",
                "approval_execution_id": execution_id,
            },
        )
        result = self.tool_harness.execute(claimed.tool_name, claimed.arguments, context)
        summary = result.output[:500] if result.output else None
        final = self.approvals.repository.finalize_execution(
            claimed.id,
            execution_id=execution_id,
            ok=result.ok,
            result_summary=summary,
            error=result.error,
        )
        if final is None:
            raise RuntimeError("approval execution state could not be finalized")
        notify("approval_execution_completed" if result.ok else "approval_execution_failed", {
            "approval_id": final.id, "execution_id": execution_id,
            "workflow_id": final.workflow_id, "request_id": final.request_id,
            "agent_id": final.agent_id, "tool_call_id": final.tool_call_id,
            "tool": final.tool_name, "ok": result.ok, "error": result.error,
        })
        return final, result
