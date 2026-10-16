"""Enforced tool execution gateway.

Execution ordering:
  resolve -> schema -> authenticate -> user scope -> rate limit -> durable audit
  -> security -> (proposal OR capability+approval binding -> concrete handler)

Ordinary agents never execute side-effect tools. Agent-visible side effects are
``propose_*`` aliases and stale direct write calls are also converted into
ProposedAction rows. Only ApprovedActionExecutor owns write capabilities.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pu.core.interfaces.security import SecurityHarness
from pu.core.interfaces.tools import ToolContext, ToolHarness, ToolResult
from pu.harness.audit import ToolAuditRecord, ToolAuditSink, action_hash, utc_now
from pu.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from pu.approval.service import HumanApprovalService


class ToolValidationError(ValueError):
    pass


class InMemoryRateLimiter:
    def __init__(self, limit: int = 120, window_seconds: int = 60):
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, principal: str, tool_name: str) -> bool:
        now = time.monotonic()
        key = (principal, tool_name)
        with self._lock:
            bucket = self._events[key]
            cutoff = now - self.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "null": lambda v: v is None,
    }.get(expected, lambda v: True)(value)


def _validate_value(name: str, value: Any, schema: dict[str, Any]) -> None:
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_type(value, item) for item in expected):
            raise ToolValidationError(f"'{name}' has invalid type")
    elif isinstance(expected, str) and not _matches_type(value, expected):
        raise ToolValidationError(f"'{name}' must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(f"'{name}' must be one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ToolValidationError(f"'{name}' is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ToolValidationError(f"'{name}' is too long")


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ToolValidationError("tool arguments must be an object")
    if schema.get("type", "object") != "object":
        raise ToolValidationError("tool root schema must be an object")
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    missing = sorted(name for name in required if name not in arguments)
    if missing:
        raise ToolValidationError(f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(name for name in arguments if name not in properties)
    strict = schema.get("additionalProperties", False) is False
    if unknown and strict:
        raise ToolValidationError(f"unknown field(s): {', '.join(unknown)}")
    normalized = dict(arguments)
    for name, value in arguments.items():
        if name in properties:
            _validate_value(name, value, properties[name])
    return normalized


class DefaultToolHarness(ToolHarness):
    def __init__(
        self,
        registry: ToolRegistry,
        security: SecurityHarness,
        audit: ToolAuditSink,
        *,
        rate_limiter: InMemoryRateLimiter | None = None,
        approval_service: HumanApprovalService | None = None,
        read_retry_attempts: int = 0,
    ):
        self.registry = registry
        self.security = security
        self.audit = audit
        self.rate_limiter = rate_limiter or InMemoryRateLimiter()
        self.approval_service = approval_service
        self.read_retry_attempts = max(0, int(read_retry_attempts))

    def _emit(self, context: ToolContext, kind: str, event: dict[str, Any]) -> None:
        if context.observer is not None:
            context.observer(kind, {
                "request_id": context.request_id,
                "workflow_id": context.workflow_id,
                "agent_id": context.agent_id,
                "tool_call_id": context.tool_call_id,
                **event,
            })

    def _record(self, context: ToolContext, tool, execution_id: str, arguments: dict[str, Any], **kwargs) -> None:
        definition = tool.definition
        self.audit.append(ToolAuditRecord(
            execution_id=execution_id,
            request_id=context.request_id,
            workflow_id=context.workflow_id,
            agent_id=context.agent_id,
            tool_call_id=context.tool_call_id,
            actor=context.actor,
            principal=context.principal,
            tool=definition.name,
            capability=definition.capability,
            required_scope=definition.required_scope,
            side_effect=definition.side_effect,
            external=definition.external,
            arguments=arguments,
            action_hash=action_hash(definition.name, arguments),
            timestamp=utc_now(),
            status=kwargs.pop("status"),
            **kwargs,
        ))

    def _blocked(
        self,
        context: ToolContext,
        tool,
        execution_id: str,
        arguments: dict[str, Any],
        *,
        policy: str,
        reason: str,
        error: str,
        stage: str | None = None,
    ) -> ToolResult:
        self._record(
            context, tool, execution_id, arguments,
            status="blocked", policy=policy, reason=reason,
        )
        self._emit(context, "tool_blocked", {
            "tool": tool.definition.name,
            "stage": stage or policy,
            "reason": reason,
            "execution_id": execution_id,
        })
        return ToolResult(
            output=f"Error: {reason}", ok=False, error=error,
            metadata={"execution_id": execution_id, "policy": policy},
        )

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        execution_id = str(uuid.uuid4())
        if context.actor == "approved_action_executor":
            tool = self.registry.resolve(name)
            proposal_requested = False
        else:
            tool, proposal_requested = self.registry.resolve_agent_name(name)
        if tool is None:
            return ToolResult(
                output=f"Error: unknown tool '{name}'", ok=False, error="unknown_tool",
                metadata={"execution_id": execution_id},
            )
        definition = tool.definition

        try:
            normalized = validate_arguments(definition.input_schema, arguments)
        except ToolValidationError as exc:
            return self._blocked(
                context, tool, execution_id,
                arguments if isinstance(arguments, dict) else {},
                policy="schema", reason=str(exc), error="schema_validation", stage="schema",
            )

        if not context.principal:
            return self._blocked(
                context, tool, execution_id, normalized,
                policy="authentication", reason="missing authenticated principal",
                error="authentication", stage="authentication",
            )

        if definition.required_scope not in context.scopes:
            return self._blocked(
                context, tool, execution_id, normalized,
                policy="scope", reason=f"principal lacks scope '{definition.required_scope}'",
                error="scope_denied", stage="scope",
            )

        if not self.rate_limiter.allow(context.principal, definition.name):
            return self._blocked(
                context, tool, execution_id, normalized,
                policy="rate_limit", reason="tool rate limit exceeded",
                error="rate_limit", stage="rate_limit",
            )

        # Side effects from ordinary agents are proposal-only. This branch runs
        # before the concrete write capability check, because the agent must not
        # own that capability at all.
        is_proposal = definition.side_effect and context.actor != "approved_action_executor"
        if is_proposal:
            if "action:propose" not in context.capabilities:
                return self._blocked(
                    context, tool, execution_id, normalized,
                    policy="capability", reason="agent lacks capability 'action:propose'",
                    error="capability_denied", stage="capability",
                )
            if self.approval_service is None:
                return self._blocked(
                    context, tool, execution_id, normalized,
                    policy="human_approval", reason="human approval service is unavailable",
                    error="approval_unavailable", stage="approval",
                )

            # Audit the exact payload before any policy or approval state change.
            self._record(context, tool, execution_id, normalized, status="attempted")
            self._emit(context, "tool_attempt", {
                "tool": definition.name,
                "requested_as": name,
                "capability": "action:propose",
                "target_capability": definition.capability,
                "scope": definition.required_scope,
                "side_effect": True,
                "external": definition.external,
                "execution_id": execution_id,
            })

            proposal_context = replace(
                context,
                metadata={**context.metadata, "proposal_only": True},
            )
            decision = self.security.authorize(definition, normalized, proposal_context)
            self._emit(context, "security", {
                "tool": definition.name,
                "allowed": decision.allowed,
                "policy": decision.policy,
                "reason": decision.reason,
                "execution_id": execution_id,
                "proposal_only": True,
            })
            if not decision.allowed:
                self._record(
                    context, tool, execution_id, normalized,
                    status="blocked", policy=decision.policy, reason=decision.reason,
                )
                return ToolResult(
                    output=f"Error: blocked by security policy ({decision.policy}): {decision.reason}",
                    ok=False, error="security_denied",
                    metadata={"execution_id": execution_id, "policy": decision.policy},
                )

            action = self.approval_service.propose(definition, normalized, context)
            self._record(
                context, tool, execution_id, normalized,
                status="approval_pending", policy="human_approval",
                reason=f"proposal {action.id} awaiting human approval",
            )
            event = {
                "approval_id": action.id,
                "status": action.status.value,
                "tool": action.tool_name,
                "action_hash": action.action_hash,
                "risk": action.risk,
                "expires_at": action.expires_at,
                "revision": action.revision,
                "execution_id": execution_id,
            }
            self._emit(context, "approval_requested", event)
            return ToolResult(
                output=(
                    f"Approval required before '{definition.name}' can execute. "
                    f"Proposal {action.id} is pending human review."
                ),
                ok=True,
                metadata={
                    "execution_id": execution_id,
                    "approval_id": action.id,
                    "approval_status": action.status.value,
                    "action_hash": action.action_hash,
                    "action_tool": action.tool_name,
                    "risk": action.risk,
                    "expires_at": action.expires_at,
                    "proposal_requested": proposal_requested,
                },
            )

        # Read tools use their own capability. Write tools can only reach this
        # branch via ApprovedActionExecutor.
        if definition.capability not in context.capabilities:
            return self._blocked(
                context, tool, execution_id, normalized,
                policy="capability", reason=f"agent lacks capability '{definition.capability}'",
                error="capability_denied", stage="capability",
            )

        if definition.side_effect:
            expected_hash = action_hash(definition.name, normalized)
            approval_id = context.metadata.get("approval_id")
            approved_hash = context.metadata.get("approval_action_hash")
            approval_status = context.metadata.get("approval_status")
            if (
                not approval_id
                or approved_hash != expected_hash
                or approval_status != "executing"
            ):
                return self._blocked(
                    context, tool, execution_id, normalized,
                    policy="approval_binding",
                    reason="side-effect execution is not bound to the approved payload",
                    error="approval_denied", stage="approval_binding",
                )

        # Durable attempt is intentionally before Security Harness and handler.
        self._record(context, tool, execution_id, normalized, status="attempted")
        self._emit(context, "tool_attempt", {
            "tool": definition.name,
            "capability": definition.capability,
            "scope": definition.required_scope,
            "side_effect": definition.side_effect,
            "external": definition.external,
            "execution_id": execution_id,
            "approval_id": context.metadata.get("approval_id"),
        })

        # Approval never bypasses the security boundary: execute-time arguments
        # are checked again after the one-shot approval has been claimed.
        decision = self.security.authorize(definition, normalized, context)
        self._emit(context, "security", {
            "tool": definition.name,
            "allowed": decision.allowed,
            "policy": decision.policy,
            "reason": decision.reason,
            "execution_id": execution_id,
            "approval_id": context.metadata.get("approval_id"),
        })
        if not decision.allowed:
            self._record(
                context, tool, execution_id, normalized,
                status="blocked", policy=decision.policy, reason=decision.reason,
            )
            return ToolResult(
                output=f"Error: blocked by security policy ({decision.policy}): {decision.reason}",
                ok=False, error="security_denied",
                metadata={"execution_id": execution_id, "policy": decision.policy},
            )

        started = time.perf_counter()
        try:
            max_attempts = 1 + (self.read_retry_attempts if definition.external and not definition.side_effect else 0)
            attempt = 0
            while True:
                attempt += 1
                try:
                    result = tool.execute(normalized, context)
                    break
                except (TimeoutError, ConnectionError, OSError) as exc:
                    if attempt >= max_attempts:
                        raise
                    self._record(
                        context, tool, execution_id, normalized, status="retrying",
                        policy="transient_retry", reason=f"{type(exc).__name__}: {exc}",
                    )
                    self._emit(context, "tool_retry", {
                        "tool": definition.name, "execution_id": execution_id,
                        "attempt": attempt, "max_attempts": max_attempts,
                        "error": type(exc).__name__,
                    })
            latency_ms = int((time.perf_counter() - started) * 1000)
            summary = f"{len(result.output)} chars"
            self._record(
                context, tool, execution_id, normalized,
                status="succeeded" if result.ok else "failed",
                policy=decision.policy,
                reason=decision.reason,
                latency_ms=latency_ms,
                result_summary=summary,
                error=result.error,
            )
            self._emit(context, "tool_complete", {
                "tool": definition.name,
                "ok": result.ok,
                "latency_ms": latency_ms,
                "execution_id": execution_id,
                "approval_id": context.metadata.get("approval_id"),
            })
            return replace(result, metadata={
                **result.metadata,
                "execution_id": execution_id,
                "approval_id": context.metadata.get("approval_id"),
            })
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            error = f"{type(exc).__name__}: {exc}"
            self._record(
                context, tool, execution_id, normalized,
                status="failed", policy=decision.policy, reason=decision.reason,
                latency_ms=latency_ms, error=error,
            )
            self._emit(context, "tool_complete", {
                "tool": definition.name,
                "ok": False,
                "latency_ms": latency_ms,
                "execution_id": execution_id,
                "approval_id": context.metadata.get("approval_id"),
                "error": type(exc).__name__,
            })
            return ToolResult(
                output=f"Error running {definition.name}: {exc}",
                ok=False,
                error="execution_error",
                metadata={
                    "execution_id": execution_id,
                    "approval_id": context.metadata.get("approval_id"),
                },
            )
