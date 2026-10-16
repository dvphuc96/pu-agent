"""AgentOps event contracts independent from JSONL/OTel implementations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 2
    trace_id: str | None = None
    request_id: str | None = None
    workflow_id: str | None = None
    agent_id: str | None = None
    tool_call_id: str | None = None
    execution_id: str | None = None
    approval_id: str | None = None
    report_id: str | None = None
    severity: str = "info"
    status: str | None = None
    duration_ms: int | None = None


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, kind: str, event: dict[str, Any]) -> None: ...


@runtime_checkable
class AgentEventRepository(Protocol):
    def append(self, event: AgentEvent) -> None: ...
    def list(self, *, limit: int = 200, workflow_id: str | None = None) -> list[dict[str, Any]]: ...
