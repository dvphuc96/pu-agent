"""Tool domain contracts shared by registry/harness/security layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


Observer = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    capability: str
    required_scope: str
    side_effect: bool = False
    external: bool = False
    version: str = "1"


@dataclass(frozen=True)
class ToolContext:
    observer: Observer | None = None
    actor: str = "agent"
    principal: str | None = None
    request_id: str | None = None
    workflow_id: str | None = None
    agent_id: str | None = None
    tool_call_id: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    output: str
    ok: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ToolContract(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult: ...


@runtime_checkable
class ToolHarness(Protocol):
    """The only supported runtime execution boundary for tool calls."""

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult: ...
