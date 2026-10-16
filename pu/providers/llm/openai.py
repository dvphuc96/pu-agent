"""OpenAI implementation of the provider-neutral LLM contract."""

from __future__ import annotations

import json
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


class OpenAILLMProvider(LLMProvider):
    provider_id = "openai"

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
                import openai
            except ImportError as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("OpenAI provider requires the 'openai' package") from exc
            client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._client = client

    def complete(self, request: LLMRequest, on_text: TextSink | None = None) -> LLMResponse:
        kwargs = self._request_kwargs(request)
        try:
            if request.stream and on_text is not None:
                return self._complete_stream(kwargs, on_text)
            raw = self._call(kwargs)
            return self._response(raw)
        except Exception as exc:
            raise normalize_provider_error(self.provider_id, exc) from exc

    def _request_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(self._messages(request.messages))
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": request.max_tokens,
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        return kwargs

    @staticmethod
    def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": str(message.get("content", "")),
                    }
                )
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if role != "assistant":
                text = "".join(
                    str(block.get("text", ""))
                    for block in (content or [])
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                out.append({"role": role, "content": text})
                continue

            text_parts: list[str] = []
            calls: list[dict[str, Any]] = []
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_call":
                    call = {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("arguments") or {}),
                        },
                    }
                    metadata = block.get("metadata") or {}
                    # OpenAI-compatible reasoning gateways can require this opaque
                    # metadata to be echoed back on the follow-up tool turn.
                    if metadata.get("extra_content") is not None:
                        call["extra_content"] = metadata["extra_content"]
                    calls.append(call)
            entry: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        return out

    def _call(self, kwargs: dict[str, Any], **extra: Any) -> Any:
        try:
            return self._client.chat.completions.create(**kwargs, **extra)
        except Exception as exc:
            low = str(exc).lower()
            if "reasoning_effort" in low:
                # Reasoning models (e.g. gpt-5.6-luna) reject function tools when
                # the server applies its default reasoning effort on
                # /v1/chat/completions; the API's documented remedy is an explicit
                # reasoning_effort='none'. Heal once — it is never sent up front,
                # because other endpoints reject unknown parameters.
                healed = {**kwargs, "reasoning_effort": "none"}
                return self._client.chat.completions.create(**healed, **extra)
            if "max_completion_tokens" not in low and "max_tokens" not in low:
                raise
            fallback = dict(kwargs)
            fallback["max_tokens"] = fallback.pop("max_completion_tokens", None)
            return self._client.chat.completions.create(**fallback, **extra)

    def _complete_stream(self, kwargs: dict[str, Any], on_text: TextSink) -> LLMResponse:
        try:
            stream = self._call(kwargs, stream=True, stream_options={"include_usage": True})
        except Exception as exc:
            # A few OpenAI-compatible endpoints reject stream_options but do stream.
            if "stream_options" not in str(exc).lower():
                raise
            stream = self._call(kwargs, stream=True)

        text_parts: list[str] = []
        tools: dict[int, dict[str, Any]] = {}
        usage = None
        finish_reason = None
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = choice.delta
            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                on_text(content)
            for tool_call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(tool_call, "index", 0) or 0)
                slot = tools.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": "", "metadata": {}},
                )
                if getattr(tool_call, "id", None):
                    slot["id"] = tool_call.id
                function = getattr(tool_call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        slot["name"] = function.name
                    if getattr(function, "arguments", None):
                        slot["arguments"] += function.arguments
                extra_content = getattr(tool_call, "extra_content", None)
                if extra_content is not None:
                    slot["metadata"]["extra_content"] = extra_content

        calls = [
            LLMToolCall(
                id=slot["id"],
                name=slot["name"],
                arguments=json.loads(slot["arguments"] or "{}"),
                metadata=slot["metadata"],
            )
            for _, slot in sorted(tools.items())
        ]
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=calls,
            usage=LLMUsage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            ),
            finish_reason="tool_use" if calls else self._finish_reason(finish_reason),
            raw=None,
        )

    @classmethod
    def _response(cls, raw: Any) -> LLMResponse:
        choices = getattr(raw, "choices", None) or []
        if not choices:
            error = getattr(raw, "error", None) or "endpoint returned no choices"
            raise RuntimeError(str(error))
        choice = choices[0]
        message = choice.message
        calls: list[LLMToolCall] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            metadata: dict[str, Any] = {}
            extra_content = getattr(tool_call, "extra_content", None)
            if extra_content is not None:
                metadata["extra_content"] = extra_content
            calls.append(
                LLMToolCall(
                    id=str(tool_call.id),
                    name=str(tool_call.function.name),
                    arguments=json.loads(tool_call.function.arguments or "{}"),
                    metadata=metadata,
                )
            )
        usage = getattr(raw, "usage", None)
        return LLMResponse(
            text=getattr(message, "content", None) or "",
            tool_calls=calls,
            usage=LLMUsage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            ),
            finish_reason="tool_use" if calls else cls._finish_reason(getattr(choice, "finish_reason", None)),
            raw=raw,
        )

    @staticmethod
    def _finish_reason(reason: str | None) -> str:
        return {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "function_call": "tool_use",
            "length": "max_tokens",
            "content_filter": "content_filter",
        }.get(reason or "", "unknown")
