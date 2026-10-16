"""DETERMINISTIC EVAL — curated Anthropic/OpenAI model shortlist."""

from __future__ import annotations

import json

import pytest

from pu.ops import catalog
from pu.ops import settings_api as d

PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ZAI_API_KEY", "GEMINI_API_KEY")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PU_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("")
    for var in PROVIDER_KEYS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("PU_PROVIDER", raising=False)
    return tmp_path


def test_pin_persists_and_marks_first_per_provider_default(home):
    d.pin_action({"action": "pin", "provider": "anthropic", "model": "claude-opus-4-8"})
    d.pin_action({"action": "pin", "provider": "anthropic", "model": "claude-sonnet-5"})
    info = d.pin_action({"action": "pin", "provider": "openai", "model": "gpt-5.5"})

    assert json.loads((home / "models.json").read_text())["pinned"] == [
        "anthropic:claude-opus-4-8", "anthropic:claude-sonnet-5", "openai:gpt-5.5"
    ]
    flags = {(p["provider"], p["model"]): p["default"] for p in info["pinned"]}
    assert flags[("anthropic", "claude-opus-4-8")] is True
    assert flags[("anthropic", "claude-sonnet-5")] is False
    assert flags[("openai", "gpt-5.5")] is True


def test_default_model_for_reads_first_pinned(home):
    d.pin_action({"action": "pin", "provider": "openai", "model": "gpt-5.5"})
    d.pin_action({"action": "pin", "provider": "openai", "model": "gpt-4.1-mini"})
    assert catalog.default_model_for("openai") == "gpt-5.5"


def test_make_default_moves_model_to_front_of_group(home):
    d.pin_action({"action": "pin", "provider": "openai", "model": "gpt-5.5"})
    d.pin_action({"action": "pin", "provider": "openai", "model": "gpt-4.1-mini"})
    d.pin_action({"action": "default", "provider": "openai", "model": "gpt-4.1-mini"})
    assert catalog.default_model_for("openai") == "gpt-4.1-mini"


def test_unpin_promotes_next_default(home):
    d.pin_action({"action": "pin", "provider": "anthropic", "model": "claude-opus-4-8"})
    d.pin_action({"action": "pin", "provider": "anthropic", "model": "claude-sonnet-5"})
    d.pin_action({"action": "unpin", "provider": "anthropic", "model": "claude-opus-4-8"})
    assert catalog.default_model_for("anthropic") == "claude-sonnet-5"


def test_default_pairs_are_product_providers_only(home):
    from pu.providers.llm.catalog import PROVIDERS

    assert set(PROVIDERS) == {"anthropic", "openai", "openrouter", "deepseek", "glm", "gemini"}
    assert PROVIDERS["anthropic"].default_pair() == ["claude-opus-4-8", "claude-sonnet-5"]
    assert PROVIDERS["openai"].default_pair() == ["gpt-5.5", "gpt-4.1-mini"]
    assert PROVIDERS["openrouter"].default_pair() == ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-26b-a4b-it:free"]
    assert PROVIDERS["deepseek"].default_pair() == ["deepseek-v4-pro"]
    assert PROVIDERS["glm"].default_pair() == ["glm-5.2", "glm-5-turbo"]
    assert PROVIDERS["gemini"].default_pair() == ["gemini-3.1-pro-preview", "gemini-3.5-flash"]


def test_defaults_apply_only_for_keyed_providers(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    info = d.settings_info()
    pairs = [(p["provider"], p["model"], p["default"]) for p in info["pinned"]]
    assert pairs == [
        ("anthropic", "claude-opus-4-8", True),
        ("anthropic", "claude-sonnet-5", False),
    ]
    assert catalog.default_model_for("openai") == ""
