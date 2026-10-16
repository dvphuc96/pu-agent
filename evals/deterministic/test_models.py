"""Deterministic coverage for the Phase-4 provider-neutral factory."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from pu.config import Settings
from pu.providers.llm.anthropic import AnthropicLLMProvider
from pu.providers.llm.catalog import PROVIDERS
from pu.providers.llm.factory import create_llm_provider
from pu.providers.llm.openai import OpenAILLMProvider


def test_supported_provider_registry_is_exact():
    assert set(PROVIDERS) == {"anthropic", "openai", "openrouter", "deepseek", "glm", "gemini"}
    assert PROVIDERS["anthropic"].provider_id == "anthropic"
    assert PROVIDERS["openai"].provider_id == "openai"


def test_defaults_are_concrete_and_tool_capable_choices():
    assert PROVIDERS["anthropic"].default_pair() == ["claude-opus-4-8", "claude-sonnet-5"]
    assert PROVIDERS["openai"].default_pair() == ["gpt-5.5", "gpt-4.1-mini"]
    assert PROVIDERS["openrouter"].default_pair() == ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-26b-a4b-it:free"]
    assert PROVIDERS["deepseek"].default_pair() == ["deepseek-v4-pro"]
    assert PROVIDERS["glm"].default_pair() == ["glm-5.2", "glm-5-turbo"]
    assert PROVIDERS["gemini"].default_pair() == ["gemini-3.1-pro-preview", "gemini-3.5-flash"]
    assert all(not model.endswith("-latest") for p in PROVIDERS.values() for model in p.default_pair())


def test_anthropic_factory_builds_adapter_and_sdk(monkeypatch, tmp_path):
    captured = {}

    class StubAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = SimpleNamespace()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=StubAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    # The repo .env is loaded into os.environ at pu.config import time; a
    # same-provider PU_MODEL would otherwise override the catalog default
    # these asserts pin (cross-provider ids are dropped, same-provider kept).
    monkeypatch.delenv("PU_MODEL", raising=False)
    monkeypatch.delenv("PU_SMALL_MODEL", raising=False)
    settings = Settings(provider="anthropic", home=tmp_path)
    provider = create_llm_provider(settings)

    assert isinstance(provider, AnthropicLLMProvider)
    assert isinstance(provider._client, StubAnthropic)
    assert captured["api_key"] == "test-anthropic"
    assert settings.model == PROVIDERS["anthropic"].default_model
    assert settings.small_model == PROVIDERS["anthropic"].default_small_model


def test_openai_factory_builds_adapter_and_sdk(monkeypatch, tmp_path):
    captured = {}

    class StubOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=StubOpenAI))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.delenv("PU_MODEL", raising=False)
    monkeypatch.delenv("PU_SMALL_MODEL", raising=False)
    settings = Settings(provider="openai", home=tmp_path)
    provider = create_llm_provider(settings)

    assert isinstance(provider, OpenAILLMProvider)
    assert isinstance(provider._client, StubOpenAI)
    assert captured["api_key"] == "test-openai"
    assert settings.model == PROVIDERS["openai"].default_model
    assert settings.small_model == PROVIDERS["openai"].default_small_model


def test_unknown_provider_fails_with_supported_choices(tmp_path):
    settings = Settings(provider="unknown", api_key="x", home=tmp_path)
    try:
        create_llm_provider(settings)
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("unknown provider should fail")
    assert all(name in message for name in PROVIDERS)


def test_foreign_inherited_model_is_dropped_on_provider_switch(monkeypatch, tmp_path):
    fake_sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("PU_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("PU_SMALL_MODEL", "claude-haiku-4-5-20251001")
    settings = Settings(
        provider="openai",
        model="claude-sonnet-5",
        small_model="claude-haiku-4-5-20251001",
        home=tmp_path,
    )
    create_llm_provider(settings, sdk_client=fake_sdk)
    assert settings.model == PROVIDERS["openai"].default_model
    assert settings.small_model == PROVIDERS["openai"].default_small_model
