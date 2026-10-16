"""OpenAI-compatible provider adapter used by non-OpenAI vendors.

OpenRouter, DeepSeek, Z.AI/GLM, and Gemini all expose an OpenAI-compatible
Chat Completions surface.  They share the stable PU ``LLMProvider`` contract
without leaking provider-specific wire formats into agents or workflows.
"""

from __future__ import annotations

from typing import Any

from pu.providers.llm.openai import OpenAILLMProvider


class OpenAICompatibleLLMProvider(OpenAILLMProvider):
    """Reuse the OpenAI wire adapter while preserving the real provider id.

    The concrete endpoint/key/model are supplied by the provider catalog and
    composition root.  Keeping ``provider_id`` vendor-specific is important for
    error normalization, tracing, usage accounting, and AgentOps.
    """

    def __init__(
        self,
        provider_id: str,
        api_key: str = "",
        *,
        base_url: str,
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        if not provider_id:
            raise ValueError("provider_id is required")
        if not base_url and client is None:
            raise ValueError(f"base_url is required for OpenAI-compatible provider '{provider_id}'")
        self.provider_id = provider_id
        super().__init__(api_key=api_key, base_url=base_url or None, timeout=timeout, client=client)
