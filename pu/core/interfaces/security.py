"""Security boundary contracts used by the enforced tool execution path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pu.core.interfaces.tools import ToolContext, ToolDefinition


@dataclass(frozen=True)
class SecurityDecision:
    allowed: bool
    reason: str
    policy: str = "unspecified"
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SecurityPolicy(Protocol):
    def evaluate(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> SecurityDecision: ...


@runtime_checkable
class SecurityHarness(Protocol):
    def authorize(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> SecurityDecision: ...
