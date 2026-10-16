"""Phase-4 conformance: Anthropic/OpenAI expose the same domain contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pu.core.interfaces.llm import (
    LLMProviderError,
    LLMRequest,
    LLMToolDefinition,
)
from pu.providers.llm.anthropic import AnthropicLLMProvider
from pu.providers.llm.openai import OpenAILLMProvider


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeAnthropicMessages:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.messages = FakeAnthropicMessages(response=response, error=error)


class FakeOpenAICompletions:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeOpenAIClient:
    def __init__(self, response=None, error=None):
        self.chat = ns(completions=FakeOpenAICompletions(response=response, error=error))


def anthropic_response(text="done", *, tool=False, stop="end_turn", in_tokens=7, out_tokens=3):
    content = [ns(type="text", text=text)] if text else []
    if tool:
        content.append(ns(type="tool_use", id="call_1", name="lookup", input={"q": "acme"}))
        stop = "tool_use"
    return ns(
        content=content,
        stop_reason=stop,
        usage=ns(input_tokens=in_tokens, output_tokens=out_tokens),
    )


def openai_response(text="done", *, tool=False, finish="stop", in_tokens=7, out_tokens=3):
    calls = []
    if tool:
        calls = [
            ns(
                id="call_1",
                function=ns(name="lookup", arguments=json.dumps({"q": "acme"})),
                extra_content={"thought_signature": "opaque"},
            )
        ]
        finish = "tool_calls"
    message = ns(content=text or None, tool_calls=calls)
    return ns(
        choices=[ns(message=message, finish_reason=finish)],
        usage=ns(prompt_tokens=in_tokens, completion_tokens=out_tokens),
    )


@pytest.mark.parametrize(
    ("provider", "expected_id"),
    [
        (AnthropicLLMProvider(client=FakeAnthropicClient(anthropic_response())), "anthropic"),
        (OpenAILLMProvider(client=FakeOpenAIClient(openai_response())), "openai"),
    ],
)
def test_same_text_usage_and_finish_contract(provider, expected_id):
    response = provider.complete(LLMRequest(model="model", messages=[{"role": "user", "content": "hi"}]))
    assert provider.provider_id == expected_id
    assert response.text == "done"
    assert response.tool_calls == []
    assert response.finish_reason == "end_turn"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3


@pytest.mark.parametrize(
    "provider",
    [
        AnthropicLLMProvider(client=FakeAnthropicClient(anthropic_response(text="", tool=True))),
        OpenAILLMProvider(client=FakeOpenAIClient(openai_response(text="", tool=True))),
    ],
)
def test_same_tool_call_contract(provider):
    response = provider.complete(LLMRequest(model="model", messages=[{"role": "user", "content": "lookup acme"}]))
    assert response.finish_reason == "tool_use"
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "lookup"
    assert call.arguments == {"q": "acme"}


def test_anthropic_serializes_neutral_tools_and_tool_results():
    raw = anthropic_response()
    client = FakeAnthropicClient(raw)
    provider = AnthropicLLMProvider(client=client)
    request = LLMRequest(
        model="claude-test",
        system="system",
        messages=[
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": [
                {"type": "tool_call", "id": "call_1", "name": "lookup", "arguments": {"q": "acme"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ],
        tools=[LLMToolDefinition("lookup", "look up", {"type": "object"})],
    )
    provider.complete(request)
    sent = client.messages.calls[0]
    assert sent["system"] == "system"
    assert sent["tools"][0]["input_schema"] == {"type": "object"}
    assert sent["messages"][1]["content"][0]["type"] == "tool_use"
    assert sent["messages"][2]["content"][0]["type"] == "tool_result"


def test_openai_serializes_neutral_tools_and_echoes_opaque_metadata():
    client = FakeOpenAIClient(openai_response())
    provider = OpenAILLMProvider(client=client)
    request = LLMRequest(
        model="gpt-test",
        system="system",
        messages=[
            {"role": "assistant", "content": [
                {
                    "type": "tool_call",
                    "id": "call_1",
                    "name": "lookup",
                    "arguments": {"q": "acme"},
                    "metadata": {"extra_content": {"thought_signature": "opaque"}},
                }
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ],
        tools=[LLMToolDefinition("lookup", "look up", {"type": "object"})],
    )
    provider.complete(request)
    sent = client.chat.completions.calls[0]
    assert sent["messages"][0] == {"role": "system", "content": "system"}
    call = sent["messages"][1]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"q": "acme"}
    assert call["extra_content"] == {"thought_signature": "opaque"}
    assert sent["messages"][2]["role"] == "tool"
    assert sent["tools"][0]["function"]["parameters"] == {"type": "object"}


def test_openai_reasoning_effort_conflict_heals_with_none():
    """Reasoning models (gpt-5.6-luna) reject function tools when the server
    applies its default reasoning effort on /v1/chat/completions. The API's
    documented remedy is an explicit reasoning_effort='none' — so: never send
    it up front (other endpoints reject unknown params), but on this exact
    conflict retry once with it."""
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError(
                    "Error code: 400 - {'error': {'message': \"Function tools with "
                    "reasoning_effort are not supported for gpt-5.6-luna in "
                    "/v1/chat/completions. To use function tools, use /v1/responses "
                    "or set reasoning_effort to 'none'.\", 'type': "
                    "'invalid_request_error', 'param': 'reasoning_effort'}}"
                )
            return openai_response()

    provider = OpenAILLMProvider(client=ns(chat=ns(completions=Completions())))
    response = provider.complete(LLMRequest(
        model="gpt-5.6-luna",
        messages=[{"role": "user", "content": "hi"}],
        tools=[LLMToolDefinition(name="lookup", description="look it up",
                                 input_schema={"type": "object"})],
    ))

    assert response.text == "done"                    # healed, not failed
    assert len(calls) == 2                            # exactly one retry
    assert "reasoning_effort" not in calls[0]         # never sent by default
    assert calls[1]["reasoning_effort"] == "none"     # the documented remedy


def test_openai_reasoning_effort_heal_failure_surfaces_loudly():
    """If the healed retry dies too, the error must surface (normalized), not
    be swallowed or retried forever."""
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("Error code: 400 - reasoning_effort is not supported")

    provider = OpenAILLMProvider(client=ns(chat=ns(completions=Completions())))
    with pytest.raises(LLMProviderError):
        provider.complete(LLMRequest(model="gpt-5.6-luna",
                                     messages=[{"role": "user", "content": "hi"}]))
    assert len(calls) == 2                            # one heal attempt, no loop


@pytest.mark.parametrize(
    ("provider", "category", "retryable"),
    [
        (AnthropicLLMProvider(client=FakeAnthropicClient(error=TimeoutError("timed out"))), "timeout", True),
        (OpenAILLMProvider(client=FakeOpenAIClient(error=RuntimeError("429 rate limit"))), "rate_limit", True),
    ],
)
def test_provider_errors_share_normalized_taxonomy(provider, category, retryable):
    with pytest.raises(LLMProviderError) as caught:
        provider.complete(LLMRequest(model="model", messages=[]))
    assert caught.value.category == category
    assert caught.value.retryable is retryable
    assert caught.value.provider_id == provider.provider_id


def test_runtime_has_no_vendor_wire_calls_outside_provider_boundary():
    root = Path(__file__).parents[2] / "pu"
    allowed = {
        "providers/llm/anthropic.py",
        "providers/llm/openai.py",
    }
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if ".messages.create(" in text or ".chat.completions.create(" in text:
            offenders.append(rel)
    assert offenders == []


def test_agents_and_workflows_do_not_branch_on_provider_id():
    root = Path(__file__).parents[2] / "pu"
    checked = [root / "app.py", root / "loop", root / "graph", root / "memory"]
    offenders = []
    for item in checked:
        paths = [item] if item.is_file() else list(item.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if 'provider == "anthropic"' in text or 'provider == "openai"' in text:
                offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


def test_anthropic_streaming_emits_text_and_returns_normalized_final():
    final = anthropic_response(text="hello")

    class Stream:
        text_stream = ["he", "llo"]
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self): return final

    class Messages:
        def stream(self, **kwargs):
            self.kwargs = kwargs
            return Stream()

    client = ns(messages=Messages())
    provider = AnthropicLLMProvider(client=client)
    deltas = []
    response = provider.complete(
        LLMRequest(model="claude-test", messages=[], stream=True),
        on_text=deltas.append,
    )
    assert deltas == ["he", "llo"]
    assert response.text == "hello"
    assert response.finish_reason == "end_turn"


def test_openai_streaming_emits_text_and_normalizes_usage():
    chunks = [
        ns(
            usage=None,
            choices=[ns(delta=ns(content="he", tool_calls=[]), finish_reason=None)],
        ),
        ns(
            usage=None,
            choices=[ns(delta=ns(content="llo", tool_calls=[]), finish_reason="stop")],
        ),
        ns(usage=ns(prompt_tokens=4, completion_tokens=2), choices=[]),
    ]

    class Completions:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            return iter(chunks)

    provider = OpenAILLMProvider(client=ns(chat=ns(completions=Completions())))
    deltas = []
    response = provider.complete(
        LLMRequest(model="gpt-test", messages=[], stream=True),
        on_text=deltas.append,
    )
    assert deltas == ["he", "llo"]
    assert response.text == "hello"
    assert response.finish_reason == "end_turn"
    assert response.usage.input_tokens == 4
    assert response.usage.output_tokens == 2
