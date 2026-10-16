"""LLM provider factory: the only product path that selects a vendor adapter."""

from __future__ import annotations

import os

from pu.config import DOTENV_PATH, Settings
from pu.core.interfaces.llm import LLMProvider
from pu.providers.llm.anthropic import AnthropicLLMProvider
from pu.providers.llm.catalog import KEY_URLS, PROVIDERS, resolve_provider_settings
from pu.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from pu.providers.llm.openai import OpenAILLMProvider


def _no_key_message(name: str, key_env: str) -> str:
    where = (
        f"Add it to {DOTENV_PATH}:\n    {key_env}=your-key-here"
        if DOTENV_PATH
        else (
            f"No .env found from {os.getcwd()} upward — create one here:\n"
            f"    echo '{key_env}=your-key-here' >> .env"
        )
    )
    url = KEY_URLS.get(name)
    head = f"No API key for provider '{name}'.\n\n"
    if url:
        head += f"  1. Get a key: {url}\n"
    return head + (
        f"  2. {where}\n\n"
        f"Other providers: {', '.join(sorted(PROVIDERS))}\n"
        "Switch with PU_PROVIDER=<name> and that provider's key."
    )


def create_llm_provider(settings: Settings, *, sdk_client=None) -> LLMProvider:
    """Resolve settings and construct exactly one provider-neutral adapter."""
    try:
        spec = resolve_provider_settings(settings)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    api_key = (settings.api_key or os.getenv(spec.key_env, "")).strip()
    if sdk_client is None and not api_key:
        raise SystemExit(_no_key_message(settings.provider, spec.key_env))
    if api_key:
        try:
            api_key.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise SystemExit(
                f"{spec.key_env} contains a non-ASCII character. "
                "Re-paste the key with no spaces or line breaks."
            ) from exc

    base_url = settings.base_url or spec.configured_base_url()
    timeout = float(os.getenv("PU_LLM_TIMEOUT", "120"))
    if settings.provider == "anthropic":
        return AnthropicLLMProvider(
            api_key=api_key, base_url=base_url, timeout=timeout, client=sdk_client
        )
    if settings.provider == "openai":
        return OpenAILLMProvider(
            api_key=api_key, base_url=base_url, timeout=timeout, client=sdk_client
        )
    if spec.openai_compatible:
        return OpenAICompatibleLLMProvider(
            provider_id=settings.provider,
            api_key=api_key,
            base_url=base_url or "",
            timeout=timeout,
            client=sdk_client,
        )
    raise SystemExit(
        f"Unknown PU_PROVIDER '{settings.provider}'. Pick one of: {', '.join(PROVIDERS)}"
    )
