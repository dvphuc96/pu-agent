"""DETERMINISTIC EVAL — calling the retained triage graph by name."""

from __future__ import annotations

from pu.ops import commands


def test_triage_is_discovered_and_takes_a_message():
    import importlib
    import inspect

    assert commands.discover() == {"triage": "pu.ops.triage:run_triage"}
    fn = importlib.import_module("pu.ops.triage").run_triage
    assert "message" in inspect.signature(fn).parameters


def test_discovery_requires_both_halves():
    import pkgutil
    import pu.graph.workflows as pkg

    modules = {m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")}
    assert modules == {"triage"}
    assert commands.discover() == {"triage": "pu.ops.triage:run_triage"}


def test_runner_table_and_commands_cannot_drift():
    from pu.ops import dashboard

    assert dashboard.WORKFLOW_RUNNERS() == commands.discover()


def test_a_leading_slash_is_a_command():
    assert commands.parse("/triage") == ("triage", "")
    assert commands.parse("  /graphs  ") == ("graphs", "")
    assert commands.parse("/triage since friday") == ("triage", "since friday")


def test_ordinary_messages_are_not_commands():
    for text in ("hello", "look in /tmp/x", "what is 3/4", "", "   ",
                 "/ gather", "the file is at /etc/hosts"):
        assert commands.parse(text) is None, text


def test_an_unknown_command_explains_itself():
    msg = commands.unknown_reply("nope")
    assert "/nope" in msg
    assert "/triage" in msg
    assert "/graphs" in msg


def test_the_listing_names_every_runnable_workflow():
    text = commands.describe()
    assert "/triage" in text


def test_running_an_unknown_name_returns_none_rather_than_raising():
    assert commands.run("definitely-not-a-workflow", lambda *a: None) is None


def test_triage_without_a_message_explains_itself_instead_of_running():
    state = commands.run("triage", lambda *a: None, "")
    assert state is not None
    assert "/triage thanks!" in state["digest"]
