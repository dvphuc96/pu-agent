"""Deterministic coverage for enabling/disabling provider cards."""

from __future__ import annotations

import os
from pathlib import Path

from pu import integrations
from pu.config import Settings
from pu.providers.llm.catalog import PROVIDERS


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("PU_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PU_DISABLED_PROVIDERS", raising=False)
    monkeypatch.delenv("PU_PROVIDER", raising=False)


def test_disabled_providers_parsing(monkeypatch):
    monkeypatch.setenv("PU_DISABLED_PROVIDERS", "openai,anthropic,,openai")
    assert Settings().disabled_providers == frozenset({"openai", "anthropic"})


def test_toggle_persists_and_roundtrips(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert integrations.apply_provider_disabled("openai", disabled=True).ok
    assert os.environ["PU_DISABLED_PROVIDERS"] == "openai"
    assert integrations.apply_provider_disabled("openai", disabled=False).ok
    assert os.environ["PU_DISABLED_PROVIDERS"] == ""


def test_toggle_rejects_unknown_and_current(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert not integrations.apply_provider_disabled("nope", disabled=True).ok
    monkeypatch.setenv("PU_PROVIDER", "anthropic")
    result = integrations.apply_provider_disabled("anthropic", disabled=True)
    assert not result.ok and "current" in result.error
    assert integrations.apply_provider_disabled("openai", disabled=True).ok


def test_settings_info_reports_only_supported_disabled_values(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("PU_DISABLED_PROVIDERS", "openai")
    from pu.ops.settings_api import settings_info

    assert settings_info()["disabled_providers"] == ["openai"]


def test_every_provider_has_a_logo():
    logos = Path(__file__).resolve().parents[2] / "pu" / "ops" / "static" / "logos"
    assert set(PROVIDERS) == {"anthropic", "openai", "openrouter", "deepseek", "glm", "gemini"}
    assert all((logos / f"{name}.svg").is_file() for name in PROVIDERS)
