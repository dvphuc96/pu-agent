"""Model catalog routing regressions; no real credentials or network calls."""

import json
import urllib.request
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pu.ops import catalog
from pu.providers.llm.catalog import PROVIDERS


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    catalog._models_cache.clear()
    for spec in PROVIDERS.values():
        monkeypatch.delenv(spec.key_env, raising=False)
        monkeypatch.delenv(spec.base_url_env, raising=False)
    monkeypatch.setattr(catalog, "remember_price", lambda *args: None)


def settings(monkeypatch, provider="openrouter", key="test-active"):
    value = SimpleNamespace(provider=provider, api_key=key, base_url=None,
                            model="active-main", small_model="active-small")
    monkeypatch.setattr(catalog, "load_settings", lambda: value)
    return value


def network(monkeypatch, rows):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"data": rows}).encode()

    def urlopen(request, timeout):
        calls.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_each_provider_uses_its_endpoint_and_auth(monkeypatch, provider):
    settings(monkeypatch, provider)
    spec = PROVIDERS[provider]
    calls = network(monkeypatch, [{"id": "test-model"}])
    result = catalog.list_models()
    assert result["listed"] is True
    expected_url = spec.catalog_for(spec.base_url) or spec.base_url.rstrip("/") + "/models"
    assert calls[0].full_url == expected_url
    headers = {k.lower(): v for k, v in calls[0].header_items()}
    if provider == "anthropic":
        assert headers["x-api-key"] == "test-active"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in headers
    else:
        assert headers["authorization"] == "Bearer test-active"
        assert "x-api-key" not in headers
        assert "anthropic-version" not in headers


def test_openrouter_preserves_vendor_ids_and_metadata(monkeypatch):
    settings(monkeypatch)
    rows = [{"id": mid, "supported_parameters": ["tools"], "context_length": 32000,
             "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
            for mid in ["aion-labs/aion-3.5", "anthropic/claude-sonnet-5", "openai/example:free"]]
    network(monkeypatch, rows)
    result = catalog.list_models()
    assert {m["id"] for m in result["models"]} == {m["id"] for m in rows}
    assert all(m["tools"] is True and m["context"] == 32000 for m in result["models"])
    assert all(m["price_in"] == 1 and m["price_out"] == 2 for m in result["models"])


def test_inactive_provider_uses_own_key_and_defaults(monkeypatch):
    settings(monkeypatch, "anthropic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router")
    calls = network(monkeypatch, [{"id": "vendor/model"}])
    result = catalog.list_models("openrouter")
    assert calls[0].get_header("Authorization") == "Bearer test-router"
    assert result["model"] == PROVIDERS["openrouter"].default_model
    assert result["small_model"] == PROVIDERS["openrouter"].default_small_model


def test_cache_is_scoped_to_credentials(monkeypatch):
    state = settings(monkeypatch)
    calls = network(monkeypatch, [{"id": "vendor/model"}])
    catalog.list_models()
    catalog.list_models()
    assert len(calls) == 1
    state.api_key = "different-key"
    catalog.list_models()
    assert len(calls) == 2
    assert "different-key" not in repr(catalog._models_cache)


def test_cache_is_scoped_to_provider_on_shared_endpoint(monkeypatch):
    state = settings(monkeypatch, "openai")
    shared = "https://example.test/models"
    for name in ("openai", "openrouter"):
        monkeypatch.setitem(PROVIDERS, name, replace(PROVIDERS[name], catalog_url=shared))
    calls = network(monkeypatch, [{"id": "model"}])
    catalog.list_models()
    state.provider = "openrouter"
    catalog.list_models()
    assert len(calls) == 2


def test_failed_catalog_falls_back_with_error(monkeypatch):
    settings(monkeypatch)

    def fail(*args, **kwargs):
        raise OSError("catalog unavailable")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    first = catalog.list_models()
    second = catalog.list_models()
    assert first == second
    assert first["listed"] is False
    assert first["error"] == "catalog unavailable"
    assert "active-main" in [m["id"] for m in first["models"]]


def test_no_catalog_keeps_only_selected_provider_defaults(monkeypatch):
    settings(monkeypatch, "openrouter")
    spec = PROVIDERS["glm"]
    monkeypatch.setitem(PROVIDERS, "glm", replace(spec, base_url=None, endpoints=()))
    result = catalog.list_models("glm")
    assert result["listed"] is False
    assert set(m["id"] for m in result["models"]) == set(spec.default_pair())
