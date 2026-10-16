"""Provider catalog/pricing conformance for the supported product providers."""

from __future__ import annotations

import re

from pu.providers.llm.catalog import KEY_URLS, PROVIDERS
from pu.ops.pricing import MODEL_CUTOFF, MODEL_PRICING, PRICING, cutoff_for, price_for


def test_every_provider_has_key_help_and_pricing():
    assert set(PROVIDERS) == set(KEY_URLS) == set(PRICING) == {"anthropic", "openai", "openrouter", "deepseek", "glm", "gemini"}
    for name, provider in PROVIDERS.items():
        assert provider.key_env.endswith("_API_KEY")
        assert provider.default_model
        assert provider.default_small_model
        assert KEY_URLS[name].startswith("https://")


def test_model_pricing_only_contains_supported_provider_families():
    allowed_prefixes = ("claude-", "gpt-")
    assert MODEL_PRICING
    assert all(model.startswith(allowed_prefixes) for model in MODEL_PRICING)


def test_every_priced_model_has_a_knowledge_cutoff():
    assert set(MODEL_PRICING) <= set(MODEL_CUTOFF)
    for cutoff in MODEL_CUTOFF.values():
        if cutoff is not None:
            assert re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", cutoff)
    assert cutoff_for("some-future-model") is None


def test_price_for_layers_model_over_provider():
    assert price_for("anthropic", "claude-opus-4-8") == MODEL_PRICING["claude-opus-4-8"]
    assert price_for("openai", "gpt-5.5") == MODEL_PRICING["gpt-5.5"]
    assert price_for("openai", "future-gpt") == PRICING["openai"]
