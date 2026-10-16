"""Provider error normalization helpers."""

from __future__ import annotations

from pu.core.interfaces.llm import LLMProviderError


def normalize_provider_error(provider_id: str, exc: Exception) -> LLMProviderError:
    """Map vendor exceptions to a stable, intentionally small taxonomy.

    We avoid importing SDK exception classes here so the module remains usable in
    environments where only one provider SDK is installed or adapters are unit-tested
    with fake clients.
    """
    name = type(exc).__name__.lower()
    message = str(exc)
    low = message.lower()

    if "auth" in name or "authentication" in low or "api key" in low or "unauthorized" in low:
        category, retryable = "authentication", False
    elif "rate" in name or "rate limit" in low or "429" in low:
        category, retryable = "rate_limit", True
    elif "timeout" in name or "timed out" in low:
        category, retryable = "timeout", True
    elif any(token in name for token in ("badrequest", "invalidrequest")) or "400" in low:
        category, retryable = "invalid_request", False
    elif "connection" in name or "connection" in low:
        category, retryable = "connection", True
    elif any(code in low for code in ("500", "502", "503", "504")) or "server" in name:
        category, retryable = "server", True
    else:
        category, retryable = "unknown", False

    return LLMProviderError(
        f"{provider_id} provider error: {message}",
        provider_id=provider_id,
        category=category,
        retryable=retryable,
        cause=exc,
    )
