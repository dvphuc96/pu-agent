"""Agent/application contracts that remain independent of provider SDKs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pu.core.interfaces.tools import ToolContext


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"in": 0, "out": 0, "calls": 0})
    reports: list[dict[str, Any]] = field(default_factory=list)
    inbox_validation: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentRunRequest:
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: Any
    max_iterations: int = 10
    max_tokens: int = 2048
    stream: bool = False
    tool_context: ToolContext | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    def run(self, request: AgentRunRequest, observer=None) -> AgentResult: ...


class BaseAgent(ABC):
    """Application-facing agent abstraction for specialized domain agents."""

    @abstractmethod
    def respond(self, user_message: str, **kwargs) -> AgentResult:
        raise NotImplementedError
