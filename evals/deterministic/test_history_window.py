"""DETERMINISTIC EVAL — working memory is a bounded sliding window.

A long-lived session can accumulate history forever, and every turn may resend
the whole thing (unbounded context ->
cost/latency climb -> eventual context-limit break). Working memory must be a
fixed window; older turns live in state.db + consolidation, not the prompt."""

from __future__ import annotations

from evals.helpers import ScriptedProvider, make_pu, response, text_block


def _gate_skip():
    return response([text_block('{"retrieve": false, "query": "", "reason": "t"}')])


def test_prompt_history_is_windowed(tmp_path, monkeypatch):
    monkeypatch.setenv("PU_HISTORY_TURNS", "3")   # keep only last 3 turns
    sent = []

    class Recorder(ScriptedProvider):
        def _create(self, **kwargs):
            # snapshot the message count NOW — run_loop mutates the same list
            # (appends the assistant reply) after this call returns
            sent.append(list(kwargs.get("messages", [])))
            return self._script.pop(0)

    # 5 turns; each turn = gate call (skip) + one loop call
    script = []
    for _ in range(5):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_pu(tmp_path / "home", llm_provider=Recorder(script))
    for i in range(5):
        app.respond(f"message number {i}")

    # the LAST loop call's messages: at most 3 turns * 2 rows + the new user
    # message = 7, and it must NOT contain the oldest turns
    last = sent[-1]
    assert len(last) <= 3 * 2 + 1, f"window not applied: {len(last)} messages"
    text_blob = " ".join(str(m.get("content", "")) for m in last)
    assert "message number 0" not in text_blob
    assert "message number 4" in text_blob   # the newest turn is present


def test_default_window_is_generous_but_finite(tmp_path, monkeypatch):
    monkeypatch.delenv("PU_HISTORY_TURNS", raising=False)
    app = make_pu(tmp_path / "home", llm_provider=ScriptedProvider([]))
    assert app.settings.history_turns == 12
