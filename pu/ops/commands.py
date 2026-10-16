"""Slash commands for deterministic graph workflows.

The dashboard exposes one named graph workflow: ``/triage <message>``. Discovery
requires a pure workflow module plus an ops binder, so later workflows can be
added without a second registry.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil


def discover() -> dict[str, str]:
    """{command name: "module:function"} for every runnable workflow.

    Import errors are swallowed per workflow: one broken optional workflow must
    not take out the whole command list (and with it the chat box).
    """
    import pu.graph.workflows as pkg

    found: dict[str, str] = {}
    for mod in pkgutil.iter_modules(pkg.__path__):
        name = mod.name
        if name.startswith("_"):
            continue
        binder = f"pu.ops.{name}"
        try:
            module = importlib.import_module(binder)
        except Exception:  # noqa: S112 — one broken optional workflow must not
            continue       # empty the command list, and with it the chat box
        if callable(getattr(module, f"run_{name}", None)):
            found[name] = f"{binder}:run_{name}"
    return found


def describe() -> str:
    """The /graphs reply. Reads the binder's docstring first line so a new
    workflow describes itself instead of needing a second registration."""
    lines = ["**Graph workflows you can run by name**", ""]
    for name, target in sorted(discover().items()):
        module_name, _, _ = target.partition(":")
        try:
            doc = (importlib.import_module(module_name).__doc__ or "").strip()
            summary = doc.splitlines()[0] if doc else ""
        except Exception:
            summary = ""
        lines.append(f"`/{name}` — {summary}" if summary else f"`/{name}`")
    lines += [
        "",
        ("`triage` is the ROUTER — it also runs itself on every message when "
         "graph workflows are on, so calling it by name just lets you watch one "
         "message choose a door. The others are procedures: they only run when "
         "you ask."),
    ]
    return "\n".join(lines)


def parse(message: str) -> tuple[str, str] | None:
    """('graphs', '') / ('triage', 'rest of line') / None if not a command.

    A leading slash only counts at the very start and with no space after it,
    so a message that merely mentions a path is still a message.
    """
    text = (message or "").strip()
    if not text.startswith("/") or text.startswith("/ "):
        return None
    head, _, rest = text[1:].partition(" ")
    return head.strip().lower(), rest.strip()


def run(name: str, emit, arg: str = "") -> dict | None:
    """Run a workflow by name, streaming its node events through `emit`.

    `arg` is whatever followed the command and is passed only to runners that
    declare a `message` parameter. Inspecting the signature keeps this a
    property of the binder instead of a second registration table.

    Returns the final state, or None if the name is unknown. Never raises: a
    bad command must answer in the chat, not kill the stream.
    """
    target = discover().get(name)
    if target is None:
        return None
    module_name, _, fn_name = target.partition(":")
    fn = getattr(importlib.import_module(module_name), fn_name)
    if "message" in inspect.signature(fn).parameters:
        return fn(observer=emit, message=arg)
    state = fn(observer=emit)
    if arg and isinstance(state, dict):
        # Preserve visibility if a future no-argument workflow is invoked with
        # text rather than silently pretending that input was consumed.
        state["ignored_argument"] = arg
    return state


def unknown_reply(name: str) -> str:
    known = sorted(discover())
    listed = ", ".join(f"`/{k}`" for k in known) or "(none installed)"
    return f"No workflow called `/{name}`.\n\nAvailable: {listed} · `/graphs` for details."
