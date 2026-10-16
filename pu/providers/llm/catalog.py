"""Supported LLM provider metadata and configuration resolution.

This module contains product metadata only; it imports no provider SDK.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pu.config import Settings


@dataclass(frozen=True)
class ProviderEndpoint:
    label: str
    base_url: str
    catalog_url: str | None = None


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    key_env: str
    display_name: str
    default_model: str
    default_small_model: str
    base_url: str | None = None
    catalog_url: str | None = None
    flagship: str = ""
    fast: str = ""
    base_url_env: str = ""
    endpoints: tuple[ProviderEndpoint, ...] = ()
    openai_compatible: bool = False

    def default_pair(self) -> list[str]:
        pair = [self.flagship or self.default_model, self.fast or self.default_small_model]
        return list(dict.fromkeys(model for model in pair if model))

    def configured_base_url(self) -> str | None:
        configured = os.getenv(self.base_url_env, "").strip() if self.base_url_env else ""
        return configured or self.base_url

    def catalog_for(self, base_url: str | None) -> str | None:
        normalized = (base_url or "").rstrip("/")
        for endpoint in self.endpoints:
            if endpoint.base_url.rstrip("/") == normalized:
                return endpoint.catalog_url
        return self.catalog_url


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        provider_id="anthropic",
        key_env="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        default_model="claude-sonnet-5",
        default_small_model="claude-haiku-4-5-20251001",
        catalog_url="https://api.anthropic.com/v1/models",
        flagship="claude-opus-4-8",
        fast="claude-sonnet-5",
        base_url_env="PU_ANTHROPIC_BASE_URL",
    ),
    "openai": ProviderSpec(
        provider_id="openai",
        key_env="OPENAI_API_KEY",
        display_name="OpenAI",
        default_model="gpt-5.5",
        default_small_model="gpt-4.1-mini",
        catalog_url="https://api.openai.com/v1/models",
        base_url_env="PU_OPENAI_BASE_URL",
    ),
    "openrouter": ProviderSpec(
        provider_id="openrouter",
        key_env="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        default_model="nvidia/nemotron-3-super-120b-a12b:free",
        default_small_model="google/gemma-4-26b-a4b-it:free",
        base_url="https://openrouter.ai/api/v1",
        catalog_url="https://openrouter.ai/api/v1/models",
        flagship="nvidia/nemotron-3-super-120b-a12b:free",
        fast="google/gemma-4-26b-a4b-it:free",
        base_url_env="PU_OPENROUTER_BASE_URL",
        openai_compatible=True,
    ),
    "deepseek": ProviderSpec(
        provider_id="deepseek",
        key_env="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        default_model="deepseek-v4-pro",
        default_small_model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        catalog_url="https://api.deepseek.com/models",
        flagship="deepseek-v4-pro",
        fast="deepseek-v4-pro",
        base_url_env="PU_DEEPSEEK_BASE_URL",
        openai_compatible=True,
    ),
    "glm": ProviderSpec(
        provider_id="glm",
        key_env="ZAI_API_KEY",
        display_name="GLM",
        default_model="glm-5.2",
        default_small_model="glm-5-turbo",
        base_url="https://api.z.ai/api/paas/v4",
        flagship="glm-5.2",
        fast="glm-5-turbo",
        base_url_env="PU_GLM_BASE_URL",
        endpoints=(
            ProviderEndpoint("General API", "https://api.z.ai/api/paas/v4"),
            ProviderEndpoint("Coding Plan", "https://api.z.ai/api/coding/paas/v4"),
        ),
        openai_compatible=True,
    ),
    "gemini": ProviderSpec(
        provider_id="gemini",
        key_env="GEMINI_API_KEY",
        display_name="Gemini",
        default_model="gemini-3.5-flash",
        default_small_model="gemini-3.1-flash-lite",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        catalog_url="https://generativelanguage.googleapis.com/v1beta/openai/models",
        flagship="gemini-3.1-pro-preview",
        fast="gemini-3.5-flash",
        base_url_env="PU_GEMINI_BASE_URL",
        openai_compatible=True,
    ),
}

KEY_URLS = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "glm": "https://z.ai/model-api",
    "gemini": "https://aistudio.google.com/apikey",
}


def provider_spec(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown PU_PROVIDER '{provider_id}'. Pick one of: {', '.join(PROVIDERS)}"
        ) from exc


def model_belongs_elsewhere(model: str, provider_id: str) -> bool:
    """Return true only when a model id clearly belongs to the other provider."""
    # In the current product, slash-qualified model ids are OpenRouter slugs
    # (for example ``anthropic/claude-sonnet-5``). Do not carry them into a
    # direct vendor adapter when switching providers.
    if "/" in model:
        return provider_id != "openrouter"
    family = model.split("-")[0].lower()
    if not family:
        return False
    owners: dict[str, str] = {}
    for name, spec in PROVIDERS.items():
        for candidate in (
            spec.default_model,
            spec.default_small_model,
            spec.flagship,
            spec.fast,
        ):
            if candidate and "/" not in candidate:
                owners[candidate.split("-")[0].lower()] = name
    owner = owners.get(family)
    return bool(owner) and owner != provider_id


def resolve_provider_settings(settings: Settings) -> ProviderSpec:
    """Fill provider model defaults while preventing inherited cross-provider ids."""
    spec = provider_spec(settings.provider)
    for attr in ("model", "small_model"):
        inherited = os.getenv(f"PU_{attr.upper()}", "").strip()
        if (
            inherited
            and getattr(settings, attr) == inherited
            and model_belongs_elsewhere(inherited, settings.provider)
        ):
            setattr(settings, attr, "")
    settings.model = settings.model or spec.default_model
    settings.small_model = settings.small_model or spec.default_small_model
    return spec
