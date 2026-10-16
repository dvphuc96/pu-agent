"""DETERMINISTIC EVAL — a max_tokens cutoff must never end a turn in silence.

Reasoning models spend output tokens thinking BEFORE they write; when the whole
budget burns mid-thought the response comes back with no text and no tool call,
finish_reason=max_tokens. run_loop used to return that as reply="" — to the
user, forty seconds of spinner and then nothing (real case: "liệt kê 49 email
chưa đọc" on a free nemotron model, 8192/8192 output tokens, empty content).
The contract under test: nudge the model once to answer concisely; if it dies
again, say so out loud. Silence is never a final reply."""

from __future__ import annotations

from evals.helpers import ScriptedProvider, response, text_block
from pu.harness import (
    DefaultSecurityHarness,
    DefaultToolHarness,
    InMemoryToolAuditSink,
)
from pu.loop.agent import run_loop
from pu.tools.registry import ToolRegistry


class Recorder(ScriptedProvider):
    """run_loop keeps mutating the same messages list after complete() returns,
    so snapshots must be taken at call time (same trick as the history-window
    eval) — requests stored by the base class would see later appends."""

    def __init__(self, script):
        super().__init__(script)
        self.sent: list[list[dict]] = []

    def _create(self, **kwargs):
        self.sent.append(list(kwargs.get("messages", [])))
        return self._script.pop(0)


def _run(provider):
    registry = ToolRegistry()
    return run_loop(
        provider=provider,
        model="test-model",
        system="You are a test agent.",
        messages=[{"role": "user", "content": "liet ke email chua doc"}],
        tools=registry,
        tool_harness=DefaultToolHarness(
            registry, DefaultSecurityHarness(), InMemoryToolAuditSink()
        ),
    )


def test_empty_max_tokens_gets_one_nudge_then_the_model_answers():
    client = Recorder([
        response([], "max_tokens"),  # whole budget burned thinking, nothing usable
        response([text_block("20 newest unread, say 'more' for the rest")]),
    ])
    result = _run(client)

    assert result.reply.startswith("20 newest unread")  # not silence
    assert len(client.sent) == 2                        # exactly one retry
    # the retry must carry a nudge the model can act on — a NEW user message,
    # not a resend of the identical prompt
    original = client.sent[0][-1]["content"]
    nudge = client.sent[1][-1]
    assert nudge["role"] == "user" and nudge["content"] != original


def test_max_tokens_twice_ends_loudly_not_silently():
    client = ScriptedProvider([
        response([], "max_tokens"),
        response([], "max_tokens"),
    ])
    result = _run(client)

    assert result.reply != ""                  # never an empty final reply
    assert "token" in result.reply.lower()     # the user learns WHY it failed
    assert len(client.requests) == 2           # one nudge retry, no thrash


def test_truncated_text_still_returns_the_partial_text():
    """max_tokens WITH visible text is a cut-off answer, not silence — the
    partial text stands as the reply (pinned so the nudge path above never
    swallows real content)."""
    client = ScriptedProvider([
        response([text_block("Here are the first 10 emai")], "max_tokens"),
    ])
    result = _run(client)

    assert result.reply.startswith("Here are the first 10 emai")
    assert len(client.requests) == 1
