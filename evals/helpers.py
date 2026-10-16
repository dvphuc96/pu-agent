"""Shared eval plumbing: a scripted provider for offline tests and live-key detection."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace

from pu.core.interfaces.llm import LLMRequest, LLMResponse, LLMToolCall, LLMUsage


def _has_key() -> bool:
    """True when the active provider has a key set."""
    from pu.config import load_settings
    from pu.providers.llm.catalog import PROVIDERS

    settings = load_settings()
    provider = PROVIDERS.get(settings.provider)
    return bool(settings.api_key or (provider and os.getenv(provider.key_env)))


HAS_KEY = _has_key()


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, args: dict, call_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def response(blocks, stop_reason="end_turn") -> LLMResponse:
    """Build a provider-neutral response while keeping compact block fixtures."""
    text: list[str] = []
    calls: list[LLMToolCall] = []
    for block in blocks:
        kind = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
        if kind == "text":
            text.append(getattr(block, "text", "") if not isinstance(block, dict) else block.get("text", ""))
        elif kind == "tool_use":
            calls.append(LLMToolCall(
                id=str(getattr(block, "id", "") if not isinstance(block, dict) else block.get("id", "")),
                name=str(getattr(block, "name", "") if not isinstance(block, dict) else block.get("name", "")),
                arguments=dict(getattr(block, "input", {}) if not isinstance(block, dict) else block.get("input", {}) or {}),
            ))
    finish = "tool_use" if calls else {
        "end_turn": "end_turn",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop_sequence",
    }.get(stop_reason, stop_reason or "unknown")
    return LLMResponse(
        text="".join(text),
        tool_calls=calls,
        usage=LLMUsage(),
        finish_reason=finish,
    )


class ScriptedProvider:
    """Provider-neutral model double that plays back a fixed response script."""

    provider_id = "scripted"

    def __init__(self, script: list):
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    def _create(self, **kwargs):
        return self._script.pop(0)

    def complete(self, request: LLMRequest, on_text=None) -> LLMResponse:
        self.requests.append(request)
        result = self._create(
            model=request.model,
            messages=request.messages,
            max_tokens=request.max_tokens,
            system=request.system,
            tools=request.tools,
            stream=request.stream,
        )
        if not isinstance(result, LLMResponse):
            raise TypeError("ScriptedProvider scripts must contain LLMResponse values")
        if on_text is not None and request.stream and result.text:
            on_text(result.text)
        return result


def make_pu(home: Path, llm_provider=None, **settings_overrides):
    """Build a PU with an isolated home dir and optional provider-neutral fake."""
    from pu.config import Settings
    from pu.core.container import create_application

    known = {f.name for f in dataclasses.fields(Settings)}
    for switch in ("google_calendar", "graph_workflows", "gmail"):
        if switch in known:
            settings_overrides.setdefault(switch, False)
    settings = Settings(home=home, **settings_overrides)
    if llm_provider is not None and not settings.api_key:
        settings.api_key = "offline"
    return create_application(settings=settings, llm_provider=llm_provider)
