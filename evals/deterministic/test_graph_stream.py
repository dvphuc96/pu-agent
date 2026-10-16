"""DETERMINISTIC EVAL — graph workflow streaming after Phase 2 pruning.

Only the triage workflow is product-supported in this phase.  These tests pin
safe workflow lookup, event serialisability and graceful stream completion.
"""

from __future__ import annotations

import json

from pu.graph import END, START, Graph, Node
from pu.ops import dashboard


def _emitter():
    out: list[tuple[str, dict]] = []
    return out, lambda kind, ev: out.append((kind, ev))


def test_an_unknown_workflow_is_refused_without_importing_anything():
    out, emit = _emitter()
    for name in ("", "nope", "pu.ops.triage", "os:system", "../../etc/passwd"):
        out.clear()
        dashboard.graph_stream({"workflow": name}, emit)
        assert len(out) == 1 and out[0][0] == "done"
        assert "unknown workflow" in out[0][1]["error"]


def test_the_runner_table_exposes_only_triage():
    assert dashboard.WORKFLOW_RUNNERS() == {"triage": "pu.ops.triage:run_triage"}


def _scripted_runner(observer=None, message=""):
    g = Graph("triage")
    g.add_node(Node("classify", lambda s: {"route": "full"}, kind="llm"))
    g.add_node(Node("answer", lambda s: {"digest": "D"}, kind="llm"))
    g.add_edge(START, "classify")
    g.add_edge("classify", "answer")
    g.add_edge("answer", END)
    from pu.graph import run_graph

    return run_graph(g, {}, observer=observer)


def test_every_event_survives_json_dumps(monkeypatch):
    import pu.ops.triage as triage_mod

    monkeypatch.setattr(triage_mod, "run_triage", _scripted_runner)
    out, emit = _emitter()
    dashboard.graph_stream({"workflow": "triage"}, emit)
    assert out
    for kind, ev in out:
        json.dumps({"kind": kind, **ev}, default=str)


def test_node_output_never_rides_along(monkeypatch):
    import pu.ops.triage as triage_mod

    monkeypatch.setattr(triage_mod, "run_triage", _scripted_runner)
    out, emit = _emitter()
    dashboard.graph_stream({"workflow": "triage"}, emit)
    for kind, ev in out:
        if kind == "node_end":
            assert set(ev) <= {"workflow", "node", "ms", "keys", "error"}


def test_the_run_ends_with_done_carrying_the_digest(monkeypatch):
    import pu.ops.triage as triage_mod

    monkeypatch.setattr(triage_mod, "run_triage", _scripted_runner)
    out, emit = _emitter()
    dashboard.graph_stream({"workflow": "triage"}, emit)
    assert out[-1][0] == "done"
    assert out[-1][1]["digest"] == "D"
    assert out[-1][1]["workflow"] == "triage"


def test_a_runner_that_explodes_reports_instead_of_500ing(monkeypatch):
    import pu.ops.triage as triage_mod

    def boom(**kw):
        raise RuntimeError("collision")

    monkeypatch.setattr(triage_mod, "run_triage", boom)
    out, emit = _emitter()
    dashboard.graph_stream({"workflow": "triage"}, emit)
    assert out[-1][0] == "done"
    assert "collision" in out[-1][1]["error"]
