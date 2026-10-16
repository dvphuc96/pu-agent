"""Provider expansion: OpenRouter, DeepSeek, GLM and Gemini stay provider-neutral."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from pu.config import Settings
from pu.providers.llm.catalog import PROVIDERS
from pu.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from pu.providers.llm.factory import create_llm_provider


EXPECTED = {
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    "glm": ("ZAI_API_KEY", "https://api.z.ai/api/paas/v4"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/"),
}


@pytest.mark.parametrize("provider_id", EXPECTED)
def test_compatible_provider_factory_uses_openai_wire_adapter(monkeypatch, tmp_path, provider_id):
    captured = {}

    class StubOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=StubOpenAI))
    key_env, base_url = EXPECTED[provider_id]
    monkeypatch.setenv(key_env, f"test-{provider_id}")
    settings = Settings(provider=provider_id, home=tmp_path)

    provider = create_llm_provider(settings)

    assert isinstance(provider, OpenAICompatibleLLMProvider)
    assert provider.provider_id == provider_id
    assert captured["api_key"] == f"test-{provider_id}"
    assert captured["base_url"].rstrip("/") == base_url.rstrip("/")
    assert settings.model == PROVIDERS[provider_id].default_model
    assert settings.small_model == PROVIDERS[provider_id].default_small_model


def test_openrouter_slug_does_not_leak_into_direct_provider(monkeypatch, tmp_path):
    fake_sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    monkeypatch.setenv("PU_MODEL", "google/gemini-3.8-flash")
    settings = Settings(
        provider="gemini",
        api_key="test",
        model="google/gemini-3.8-flash",
        small_model="",
        home=tmp_path,
    )
    create_llm_provider(settings, sdk_client=fake_sdk)
    assert settings.model == PROVIDERS["gemini"].default_model


def test_provider_display_names_are_operator_friendly():
    assert {name: spec.display_name for name, spec in PROVIDERS.items()} == {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "openrouter": "OpenRouter",
        "deepseek": "DeepSeek",
        "glm": "GLM",
        "gemini": "Gemini",
    }
