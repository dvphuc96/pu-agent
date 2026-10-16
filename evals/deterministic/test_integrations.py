"""Deterministic coverage for the Phase-2 Connections registry."""

from __future__ import annotations

import os
import socket

import pytest

from pu import integrations
from pu.integrations import IntegrationState, IntegrationStatus
from pu.providers.llm.catalog import PROVIDERS


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("PU_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    integrations._HEALTH = None
    integrations._reset_import_cache()


def test_registry_contains_only_supported_product_connections():
    items = integrations.registry()
    keys = {item.key for item in items}
    assert keys == {
        "anthropic", "openai", "openrouter", "deepseek", "glm", "gemini",
        "google_calendar", "google_drive", "gmail", "tavily", "otel",
    }
    assert {item.key for item in items if item.group == "AI Providers"} == set(PROVIDERS)
    assert len(keys) == len(items)


def test_registry_field_contract():
    for item in integrations.registry():
        assert callable(item.enabled)
        for field in item.env:
            if field.kind is integrations.FieldKind.CHOICE:
                assert field.options
            if field.secret:
                assert field.kind is integrations.FieldKind.TEXT


def test_status_masks_secrets_and_persists_health(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-1234")
    view = next(v for v in integrations.list_integrations() if v.key == "openai")
    assert view.status.state is IntegrationState.CONFIGURED
    assert view.fields[0].value == ""
    assert view.fields[0].last4 == "1234"

    integrations.record_health("openai", IntegrationStatus(IntegrationState.CONNECTED))
    integrations._HEALTH = None
    view = next(v for v in integrations.list_integrations() if v.key == "openai")
    assert view.status.state is IntegrationState.CONNECTED

    integrations.invalidate_health("openai")
    view = next(v for v in integrations.list_integrations() if v.key == "openai")
    assert view.status.state is IntegrationState.CONFIGURED


def test_connections_exclude_ai_providers():
    assert {v.key for v in integrations.list_connections()} == {
        "google_calendar", "google_drive", "gmail", "tavily", "otel"
    }
    assert {v.key for v in integrations.list_providers()} == {"anthropic", "openai", "openrouter", "deepseek", "glm", "gemini"}


def test_otel_probe_checks_configured_collector_tcp_port(monkeypatch):
    captured = {}

    class Connection:
        def close(self):
            captured["closed"] = True

    def connect(address, timeout):
        captured.update(address=address, timeout=timeout)
        return Connection()

    monkeypatch.setattr(socket, "create_connection", connect)
    integrations._otel_probe({"OTEL_EXPORTER_OTLP_ENDPOINT": "localhost:4317"})
    assert captured == {"address": ("localhost", 4317), "timeout": 3, "closed": True}


def test_otel_probe_rejects_endpoint_without_tcp_port():
    with pytest.raises(ValueError, match="host:port"):
        integrations._otel_probe({"OTEL_EXPORTER_OTLP_ENDPOINT": "localhost"})


def test_apply_rejects_unknown_and_can_clear_secret(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("TAVILY_API_KEY=old-secret\n")
    monkeypatch.setenv("TAVILY_API_KEY", "old-secret")
    assert not integrations.apply_integration("tavily", {"NOPE": "x"}).ok

    result = integrations.apply_integration(
        "tavily", {}, ("TAVILY_API_KEY",), force=True
    )
    assert result.ok
    assert "TAVILY_API_KEY" not in os.environ
    assert "TAVILY_API_KEY" not in (tmp_path / ".env").read_text()


def test_probe_table_has_no_removed_integrations():
    retained = {"google_calendar", "google_drive", "gmail", "tavily", "otel"}
    for item in integrations.INTEGRATIONS:
        probed = integrations._probed(item)
        assert probed.key in retained
        assert callable(probed.probe)


def test_apply_keeps_connected_when_agent_reload_probe_passes(monkeypatch, tmp_path):
    """Regression: Save on an AGENT-reload integration (gmail/calendar/drive)
    runs the probe BEFORE writing, but `elif tested` could never fire after the
    rebuild branch — so a passing probe was discarded and the status dropped
    from CONNECTED back to "configured · not tested" on every Save."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("PU_GMAIL", "1")
    integrations._IMPORT_OK["googleapiclient"] = True  # extra installed, no SDK needed here
    monkeypatch.setattr(integrations, "_gmail_probe", lambda values: None)  # probe passes
    monkeypatch.setattr("pu.ops.browser_agent.rebuild", lambda: None)  # agent rebuild OK

    result = integrations.apply_integration("gmail", {"PU_GMAIL": "1"})
    assert result.ok
    view = next(v for v in integrations.list_integrations() if v.key == "gmail")
    assert view.status.state is IntegrationState.CONNECTED
