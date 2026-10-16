"""Anthropic implementation of the provider-neutral LLM contract."""

from __future__ import annotations

from typing import Any

from pu.core.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
    TextSink,
)
from pu.providers.llm.errors import normalize_provider_error


class AnthropicLLMProvider(LLMProvider):
    provider_id = "anthropic"

    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("Anthropic provider requires the 'anthropic' package") from exc
            kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
            if base_url:
                kwargs["base_url"] = base_url
            client = anthropic.Anthropic(**kwargs)
        self._client = client

    def complete(self, request: LLMRequest, on_text: TextSink | None = None) -> LLMResponse:
        kwargs = {
            "model": request.model,
            "messages": self._messages(request.messages),
            "max_tokens": request.max_tokens,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
        try:
            if request.stream and on_text is not None and hasattr(self._client.messages, "stream"):
                with self._client.messages.stream(**kwargs) as stream:
                    for delta in stream.text_stream:
                        on_text(delta)
                    raw = stream.get_final_message()
            else:
                raw = self._client.messages.create(**kwargs)
            return self._response(raw)
        except Exception as exc:
            raise normalize_provider_error(self.provider_id, exc) from exc

    @staticmethod
    def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tools() -> None:
            nonlocal pending_tool_results
            if pending_tool_results:
                out.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for message in messages:
            role = message.get("role")
            if role == "tool":
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": str(message.get("content", "")),
                    }
                )
                continue

            flush_tools()
            content = message.get("content", "")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue

            blocks: list[dict[str, Any]] = []
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    blocks.append({"type": "text", "text": str(block.get("text", ""))})
                elif kind == "tool_call":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": block.get("arguments") or {},
                        }
                    )
            out.append({"role": role, "content": blocks})
        flush_tools()
        return out

    @staticmethod
    def _response(raw: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for block in getattr(raw, "content", []) or []:
            kind = getattr(block, "type", "")
            if kind == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif kind == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
        usage = getattr(raw, "usage", None)
        reason = getattr(raw, "stop_reason", None) or "unknown"
        finish = {
            "end_turn": "end_turn",
            "tool_use": "tool_use",
            "max_tokens": "max_tokens",
            "stop_sequence": "stop_sequence",
        }.get(reason, "unknown")
        if tool_calls:
            finish = "tool_use"
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=LLMUsage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            finish_reason=finish,
            raw=raw,
        )
