"""Durable AgentOps event bus, metrics projection and SLO evaluation."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from pu.config import Settings
from pu.core.interfaces.events import AgentEvent, AgentEventRepository, EventPublisher


def _status(kind: str, payload: dict[str, Any]) -> str | None:
    if payload.get("ok") is False or payload.get("allowed") is False:
        return "failed"
    if kind.endswith("_failed") or kind in {"workflow_failed", "tool_blocked"}:
        return "failed"
    if kind.endswith("_completed") or payload.get("ok") is True:
        return "succeeded"
    return payload.get("status") or payload.get("gate_status")


def _severity(kind: str, payload: dict[str, Any]) -> str:
    status = _status(kind, payload)
    if status in {"failed", "fail", "blocked"} or kind in {"tool_blocked", "workflow_failed"}:
        return "warning"
    return "info"


class AgentOpsHarness(EventPublisher):
    """Single event boundary for JSONL/OTel tracing + durable structured events."""

    def __init__(self, repository: AgentEventRepository, tracer=None):
        self.repository = repository
        self.tracer = tracer

    def publish(self, kind: str, event: dict[str, Any]) -> None:
        payload = dict(event or {})
        duration = payload.get("latency_ms", payload.get("duration_ms", payload.get("ms")))
        try:
            duration_ms = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_ms = None
        workflow_id = payload.get("workflow_id")
        request_id = payload.get("request_id")
        structured = AgentEvent(
            kind=kind,
            payload=payload,
            trace_id=payload.get("trace_id") or workflow_id or request_id,
            request_id=request_id,
            workflow_id=workflow_id,
            agent_id=payload.get("agent_id"),
            tool_call_id=payload.get("tool_call_id"),
            execution_id=payload.get("execution_id"),
            approval_id=payload.get("approval_id"),
            report_id=payload.get("report_id"),
            severity=_severity(kind, payload),
            status=_status(kind, payload),
            duration_ms=duration_ms,
        )
        self.repository.append(structured)
        if self.tracer is not None:
            self.tracer.event(kind, {
                "event_id": structured.id,
                "schema_version": structured.schema_version,
                **payload,
            })


@dataclass(frozen=True)
class SLOStatus:
    name: str
    value: float
    target: float
    comparator: str
    ok: bool
    unit: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SLOEvaluator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, metrics: dict[str, Any]) -> dict[str, Any]:
        checks = [
            SLOStatus(
                "llm_p95_latency",
                float(metrics.get("llm_p95_ms", 0)),
                float(self.settings.slo_llm_p95_ms),
                "<=", float(metrics.get("llm_p95_ms", 0)) <= self.settings.slo_llm_p95_ms,
                "ms",
            ),
            SLOStatus(
                "tool_error_rate",
                float(metrics.get("tool_error_rate", 0)),
                float(self.settings.slo_tool_error_rate),
                "<=", float(metrics.get("tool_error_rate", 0)) <= self.settings.slo_tool_error_rate,
                "ratio",
            ),
            SLOStatus(
                "workflow_success_rate",
                float(metrics.get("workflow_success_rate", 1)),
                float(self.settings.slo_workflow_success_rate),
                ">=", float(metrics.get("workflow_success_rate", 1)) >= self.settings.slo_workflow_success_rate,
                "ratio",
            ),
            SLOStatus(
                "approval_failure_rate",
                float(metrics.get("approval_failure_rate", 0)),
                float(self.settings.slo_approval_failure_rate),
                "<=", float(metrics.get("approval_failure_rate", 0)) <= self.settings.slo_approval_failure_rate,
                "ratio",
            ),
            SLOStatus(
                "security_blocks",
                float(metrics.get("security_blocks", 0)),
                float(self.settings.slo_security_blocks_max),
                "<=", float(metrics.get("security_blocks", 0)) <= self.settings.slo_security_blocks_max,
                "events",
            ),
        ]
        return {
            "healthy": all(check.ok for check in checks),
            "checks": [check.to_dict() for check in checks],
            "alerts": [check.to_dict() for check in checks if not check.ok],
        }


def prometheus_text(metrics: dict[str, Any], slo: dict[str, Any]) -> str:
    """Dependency-free Prometheus exposition backed by durable AgentOps events."""
    lines = [
        "# HELP pu_agentops_events_total Durable structured AgentOps events.",
        "# TYPE pu_agentops_events_total counter",
        f"pu_agentops_events_total {int(metrics.get('events', 0))}",
        "# HELP pu_llm_p95_latency_ms Observed LLM p95 latency in milliseconds.",
        "# TYPE pu_llm_p95_latency_ms gauge",
        f"pu_llm_p95_latency_ms {float(metrics.get('llm_p95_ms', 0))}",
        "# HELP pu_tool_error_rate Tool execution error ratio.",
        "# TYPE pu_tool_error_rate gauge",
        f"pu_tool_error_rate {float(metrics.get('tool_error_rate', 0))}",
        "# HELP pu_workflow_success_rate Workflow success ratio.",
        "# TYPE pu_workflow_success_rate gauge",
        f"pu_workflow_success_rate {float(metrics.get('workflow_success_rate', 1))}",
        "# HELP pu_approval_failure_rate Approved action execution failure ratio.",
        "# TYPE pu_approval_failure_rate gauge",
        f"pu_approval_failure_rate {float(metrics.get('approval_failure_rate', 0))}",
        "# HELP pu_security_blocks_total Security-policy blocks in retained event window.",
        "# TYPE pu_security_blocks_total gauge",
        f"pu_security_blocks_total {int(metrics.get('security_blocks', 0))}",
        "# HELP pu_slo_healthy Whether every configured SLO is currently healthy.",
        "# TYPE pu_slo_healthy gauge",
        f"pu_slo_healthy {1 if slo.get('healthy') else 0}",
    ]
    for check in slo.get("checks", []):
        label = json.dumps(check["name"])
        lines.append(f"pu_slo_check_ok{{name={label}}} {1 if check['ok'] else 0}")
    return "\n".join(lines) + "\n"
