"""DETERMINISTIC EVAL — completion scoring (pu.ops.scoring).

These tests pin the deterministic completion-scoring contract: checklist logic
and matching a free-text prompt to its known evaluation case.
"""

from __future__ import annotations

from pu.ops import scoring


def _tc(*names):
    return [{"tool": n, "args": {}} for n in names]


def test_expect_tool_none_wants_no_call():
    case = {"expect_tool": None}
    assert scoring.check_case(case, []) == (True, "no tool expected")
    ok, why = scoring.check_case(case, _tc("create_event"))
    assert not ok and "create_event" in why          # acted on a non-command


def test_expect_tool_must_fire():
    case = {"expect_tool": "create_event"}
    assert scoring.check_case(case, _tc("create_event"))[0]
    assert not scoring.check_case(case, _tc("save_note"))[0]


def test_expect_in_args_substring():
    case = {"expect_tool": "create_event", "expect_in_args": {"title": "sam"}}
    assert scoring.check_case(case, [{"tool": "create_event", "args": {"title": "Dinner w/ Sam"}}])[0]
    ok, why = scoring.check_case(case, [{"tool": "create_event", "args": {"title": "lunch"}}])
    assert not ok and "sam" in why


def test_min_tool_calls_floor():
    case = {"expect_tool": "create_event", "expect_min_tool_calls": 3}
    ok, why = scoring.check_case(case, _tc("create_event"))
    assert not ok and ">= 3" in why
    assert scoring.check_case(case, _tc("create_event", "create_event", "create_event"))[0]


def test_case_for_message_matches_trimmed_input():
    cases = [{"id": "a", "input": "Block three 25-minute focus sessions tomorrow morning"}]
    assert scoring.case_for_message("  Block three 25-minute focus sessions tomorrow morning ",
                                    cases)["id"] == "a"
    assert scoring.case_for_message("something else entirely", cases) is None


def test_real_dataset_loads_and_every_case_is_scoreable():
    cases = scoring.load_cases()
    assert len(cases) >= 11
    for c in cases:                       # every seeded case must have the fields the scorer reads
        assert "id" in c and "input" in c and "expect_tool" in c
