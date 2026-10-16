"""The model catalog — what you can actually run, and the shortlist you curated.

Two related jobs, both feeding the settings model picker:

1. **What exists.** `list_models()` asks the selected provider's official
   model catalog when available, with a short cache and a fallback to known
   defaults if the catalog is unavailable.

2. **What you chose.** `.pu/models.json` holds an ordered `provider:model`
   shortlist. The chat switcher shows exactly these — the built-in defaults are
   a starting point, never the menu. The first pinned model for a provider is
   that provider's default when you switch to it.

Writing the shortlist lives here (`save_pinned`); the pin/unpin HTTP action
lives in settings_api, because its reply is a whole settings payload. That
keeps the dependency pointing one way: settings_api -> catalog, never back.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pu.config import load_settings
from pu.ops.pricing import remember_price

_models_cache: dict[tuple[str, str, str], tuple[float, list, str | None]] = {}


def _known_default_ids(prov, out: dict, is_active: bool) -> list[dict]:
    """Best-effort model list when the live catalog is unreachable: the provider's
    flagship + fast + loop/gate defaults — so the showcase model (e.g. opus-4.8)
    is offered too, not just the two loop defaults — plus the active model when
    this is the active provider."""
    ids = [*(prov.default_pair() if prov else []),
           prov.default_model if prov else "", prov.default_small_model if prov else ""]
    if is_active:
        ids = [out.get("model"), out.get("small_model"), *ids]
    return [{"id": m} for m in dict.fromkeys(m for m in ids if m)]


def list_models(provider: str | None = None, *, use_cache: bool = True) -> dict:
    """Model ids available on a provider, for the settings model picker — the
    defaults are starting points, never the menu. Pass `provider` to list ANY
    provider's catalog (the "Your models" add-row picks a provider first);
    without it, the ACTIVE provider is used. Providers use official catalog
    URLs when declared; a provider-scoped OpenAI-compatible endpoint may expose
    `/models`. Cached 5 minutes."""
    import time
    import urllib.request

    from pu.providers.llm.catalog import PROVIDERS

    s = load_settings()
    # An explicit provider overrides the active one (and its custom base_url:
    # custom base URLs are provider-scoped).
    name = provider or s.provider
    prov = PROVIDERS.get(name)
    base = ((s.base_url if name == s.provider else None)
            or (prov.configured_base_url() if prov else None))
    out = {
        "provider": name,
        "model": (s.model if name == s.provider else "") or (prov.default_model if prov else ""),
        "small_model": (s.small_model if name == s.provider else "") or (prov.default_small_model if prov else ""),
        "endpoint": base or name,
    }
    # Where can this provider's models be listed? An explicit catalog_url wins
    # Official catalog URL wins; a custom OpenAI-compatible base URL may expose
    # /models; otherwise fall back to the known defaults.
    catalog_url = prov.catalog_for(base) if prov is not None else None
    if catalog_url:
        url = catalog_url
    elif prov is not None and (prov.provider_id == "openai" or prov.openai_compatible) and base:
        url = base.rstrip("/") + "/models"
    else:
        # No catalog endpoint: fall back to the provider's own known defaults
        # (flagship + fast + loop/gate), not just the active model.
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider)}

    # Provider and credential changes must not reuse another catalog.
    key = ((s.api_key if name == s.provider else "") or os.getenv(prov.key_env, "")).strip()
    cache_key = (name, url, hashlib.sha256(key.encode("utf-8")).hexdigest())
    cached = _models_cache.get(cache_key) if use_cache else None
    if cached and time.time() - cached[0] < 300:
        _ts, cmodels, cerr = cached          # cerr None on a real listing
        r = {**out, "listed": cerr is None, "models": cmodels}
        if cerr:
            r["error"] = cerr
        return r
    # HTTP headers must be latin-1; a key with a stray non-ASCII char (a smart
    # arrow/quote or a line-break from a bad paste) would otherwise crash the
    # whole listing with an opaque codec error and silently drop back to two
    # defaults. Catch it here with a message that actually says how to fix it.
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        msg = (f"{prov.key_env} contains a non-ASCII character — re-paste the key "
               f"(no spaces, line breaks, or arrows).")
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider), "error": msg}
    # Keep authentication scoped to the selected provider. Gateways may use
    # Anthropic headers to choose a protocol even on OpenAI-compatible URLs.
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PU)"}
    if name == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        # Surface the server's actual response body when available, not
        # just "HTTP Error 403" — an HTTPError carries the body on .read().
        msg = str(exc)
        try:
            msg = f"{msg} — {exc.read().decode()[:160]}"
        except Exception:
            pass
        # still offer the provider's known defaults so the picker isn't empty
        known = _known_default_ids(prov, out, name == s.provider)
        # cache the failure (defaults + reason) for ~1 minute so an unreachable
        # catalog doesn't stall every 5-second dashboard poll for 10s — and so a
        # cache hit still shows the defaults and the reason, not a blank list.
        _models_cache[cache_key] = (time.time() - 240, known, msg)
        return {**out, "listed": False, "models": known, "error": msg}
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        params = m.get("supported_parameters")
        entry = {
            "id": mid,
            "free": mid.endswith(":free") or pricing.get("prompt") == "0",
            # None means the endpoint does not publish this metadata
            "tools": ("tools" in params) if params is not None else None,
            # reasoning models spend tokens thinking out loud, which breaks the
            # gate's tiny budget: the UI steers them away from the gate slot
            "reasoning": ("reasoning" in params) if params is not None else None,
            "context": m.get("context_length") or m.get("context_window"),
        }
        try:
            # If pricing is published as $/token strings, keep $/M for display + cost
            pin, pout = float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
            remember_price(mid, pin, pout)
            entry["price_in"], entry["price_out"] = round(pin, 3), round(pout, 3)
        except (KeyError, TypeError, ValueError):
            pass
        models.append(entry)
    models.sort(key=lambda x: (not x["free"], x["tools"] is False, x["id"]))
    _models_cache[cache_key] = (time.time(), models, None)   # None error = a real listing
    return {**out, "listed": True, "models": models}


def _models_json() -> Path:
    return load_settings().home / "models.json"


def default_pinned_specs() -> list[str]:
    """Starter shortlist before the user has curated their own: flagship + fast
    for every provider that has a key set (so the switcher only shows models you
    can actually use). Flagship comes first, so it's that provider's default."""
    from pu.providers.llm.catalog import PROVIDERS

    specs = []
    for name, prov in PROVIDERS.items():
        if os.getenv(prov.key_env):
            specs += [f"{name}:{m}" for m in prov.default_pair()]
    return specs


def pinned_specs() -> list[str]:
    """The user's curated 'provider:model' shortlist (ordered), from
    .pu/models.json. The chat switcher shows exactly these. Before they've
    saved anything, fall back to the flagship+fast defaults."""
    p = _models_json()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("pinned", [])
        except (json.JSONDecodeError, OSError):
            pass
    return default_pinned_specs()


def default_model_for(provider: str) -> str:
    """A provider's default model = the FIRST one the user pinned for it.
    Empty string means 'use the provider's built-in default'."""
    for spec in pinned_specs():
        p, _, m = spec.partition(":")
        if p == provider and m:
            return m
    return ""


def save_pinned(specs: list[str]) -> None:
    """Persist the curated shortlist, in order. The ONLY writer of models.json —
    keep it that way so the file has one shape and one owner."""
    path = _models_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pinned": specs}, indent=1))
