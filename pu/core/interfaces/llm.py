"""Provider-neutral LLM contracts used by every agent/runtime path.

Vendor SDK objects must never cross this boundary. Concrete provider adapters
translate their wire formats into these stable domain objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

TextSink = Callable[[str], None]


@dataclass(frozen=True)
class LLMToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Opaque provider metadata may need to be echoed on the next request
    # (for example reasoning/thought signatures). Agents never interpret it.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str = ""
    # Neutral message shape:
    #   {"role": "user"|"assistant", "content": str | [neutral blocks]}
    #   {"role": "tool", "tool_call_id": str, "content": str}
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[LLMToolDefinition] = field(default_factory=list)
    max_tokens: int = 2048
    stream: bool = False


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    # Normalized values: end_turn | tool_use | max_tokens | stop_sequence |
    # content_filter | error | unknown
    finish_reason: str = "end_turn"
    # Kept only for diagnostics. Runtime/application code must not depend on it.
    raw: Any = None


class LLMProviderError(RuntimeError):
    """Normalized provider failure with a stable category for policy/retry code."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        category: str = "unknown",
        retryable: bool = False,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.category = category
        self.retryable = retryable
        self.cause = cause


@runtime_checkable
class LLMProvider(Protocol):
    """Stable contract agents depend on; vendor wire formats stay in adapters."""

    @property
    def provider_id(self) -> str: ...

    def complete(self, request: LLMRequest, on_text: TextSink | None = None) -> LLMResponse: ...
