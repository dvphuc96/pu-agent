"""Dashboard — every pillar on one local page. Zero new dependencies.

    make dashboard        # → http://localhost:9000

One stdlib HTTP server reading the files PU already writes:
  loop + harness   traces/*.jsonl   (turns, gate decisions, tool calls, tokens)
  memory           state.db         (facts, episodes, chat log, consolidation)
  tools            state.db + calendar.ics + outbox/
  eval             eval_report.json (written by `make gate`)

The overview mirrors the architecture diagram — every box is clickable and
opens that section's live data. The chat dock is a real gateway: type (or speak)
a message and watch the same harness (gate, loop, tools, memory) that the CLI/
CLI surface drives through the same loop and tracing path.

The frontend is plain static files (static/index.html + style.css + app.js)
served as-is — no build step, no framework. This file is just the server + API.
Bound to 127.0.0.1 only. For deep trace waterfalls use Phoenix (`make trace`).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pu.config import load_settings
from pu.db import connect
from pu.integrations import (
    apply_integration,
    apply_provider,
    apply_provider_disabled,
    list_connections,
    list_providers,
    test_integration,
)
from pu.ops import browser_agent, commands
from pu.ops.agentops import SLOEvaluator, prometheus_text
from pu.ops.browser_agent import agent_lock, dash_session, get_agent, maybe_rotate_session
from pu.ops.catalog import list_models
from pu.ops.pricing import price_for, usage_summary
from pu.ops.settings_api import apply_settings, pin_action, settings_info
from pu.ops.tracing import TraceEncodingError, iter_trace_lines
from pu.persistence import (
    SQLiteAgentEventRepository,
    SQLiteDatabase,
    SQLiteToolAuditSink,
    SQLiteWorkflowRepository,
)

PORT = 9000
# The frontend lives in its own files (static/index.html + style.css + app.js),
# served as-is by this stdlib server — no build step, no framework. Edit those
# to change the UI; edit this file to change the server/API.
STATIC = Path(__file__).resolve().parent / "static"


def chat(message: str) -> dict:
    """One turn, one JSON result — the non-streaming door to the same room.

    The dashboard itself uses /api/chat/stream; this exists for scripts and for
    `curl`. It deliberately does NOT reimplement the turn: it drives chat_stream
    and keeps the final "done" payload, because the two used to be separate
    copies of the same 25 lines and had already drifted (the streaming one
    reported which model answered, this one didn't). One implementation means
    they cannot disagree again.
    """
    final: dict = {}

    def collect_done(kind: str, ev: dict) -> None:
        if kind == "done":
            final.update(ev)

    chat_stream(message, collect_done)
    return final


def chat_stream(message: str, emit) -> None:
    """Run one turn, calling emit(kind, event) for every harness event AS it
    happens — gate decision, tool calls, and the reply text token by token —
    so the browser can show the same live execution stream as the CLI. Ends
    with a 'done' event carrying the final structured result.

    A leading slash calls a graph workflow BY NAME instead of running a turn.
    Both doors end in the same 'done' event, so the chat renders the answer the
    same way whether the harness routed it or you named the shape yourself."""
    command = commands.parse(message)
    if command is not None:
        _run_command(command, emit)
        return

    events: list[dict] = []

    def observer(kind, ev):
        if kind in ("gate", "consolidation", "route", "triage"):
            events.append({"kind": kind, **ev})
        emit(kind, ev)

    with agent_lock:
        agent = get_agent()
        maybe_rotate_session(agent)
        start = datetime.now(UTC)
        result = agent.respond(message, observer=observer, source="dashboard", stream=True)
        latency_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)

    gate = next((e for e in events if e["kind"] == "gate"), None)
    cons = next((e for e in events if e["kind"] == "consolidation"), None)
    route = next((e for e in events if e["kind"] == "route"), None)
    triage = next((e for e in events if e["kind"] == "triage"), None)
    quick = bool(route) and route.get("target") == "quick_reply"
    emit("done", {
        "reply": result.reply,
        "reports": result.reports,
        "inbox_validation": result.inbox_validation,
        "gate": {"decision": gate["decision"], "reason": gate.get("reason")} if gate else None,
        "graph": ({"workflow": route.get("workflow", "triage"),
                   "route": "quick" if quick else "full",
                   "reason": (triage or {}).get("reason", "")} if route else None),
        "tools": [{"tool": c["tool"], "args": c["args"], "output": c["output"],
                   "status": "warn" if c.get("approval_id") else _tool_status(c["output"]),
                   "summary": (c["output"] or "").split(". ")[0][:120],
                   "approval_id": c.get("approval_id"),
                   "approval_status": c.get("approval_status"),
                   "action_tool": c.get("action_tool")} for c in result.tool_calls],
        "consolidation": {"new_facts": cons["new_facts"]} if cons else None,
        "iterations": result.iterations,
        "usage": result.usage,
        "latency_ms": latency_ms,
        # which brain answered — shown per card; a quick graph turn was the small model
        "model": agent.settings.small_model if quick else agent.settings.model,
    })


# A NAME -> runner table, never a dynamic import of whatever the browser sent.
# "run the workflow the client named" is one careless refactor away from "import
# and call whatever string arrives", so the indirection is a dict on purpose.
def WORKFLOW_RUNNERS() -> dict[str, str]:  # noqa: N802 — reads as a table
    """Discovered, not hand-listed. A hardcoded table and a slash-command list
    are two registries of the same fact, and they drift."""
    return commands.discover()


def graph_stream(payload: dict, emit) -> None:
    """Run a graph workflow, streaming its node events as SSE.

    Only the engine's own events go out — graph_start / node_start / node_end /
    route / graph_end. They already carry `workflow` and `node`, which is all a
    card needs, and they carry no node OUTPUT, so a digest can never leak into
    a frame. Unlike the Arena this needs no lock of its own: run_graph already
    serialises notify() behind one (engine.py), so events arrive whole.
    """
    name = (payload.get("workflow") or "").strip()
    target = WORKFLOW_RUNNERS().get(name)
    if target is None:
        emit("done", {"error": f"unknown workflow '{name}'"})
        return
    module_name, _, fn_name = target.partition(":")
    try:
        import importlib

        run = getattr(importlib.import_module(module_name), fn_name)
        state = run(observer=lambda kind, ev: emit(kind, ev))
        emit("done", {
            "workflow": name,
            "digest": (state.get("digest") or "")[:4000],
            "draft_path": state.get("draft_path", ""),
            "errors": state.get("errors") or {},
        })
    except Exception as exc:
        # Includes GraphStateCollision, which run_graph raises OUT (unlike node
        # errors) — better shown in the card than dropped on the floor.
        emit("done", {"error": f"{type(exc).__name__}: {exc}"})


def _run_command(command: tuple[str, str], emit) -> None:
    """Handle `/name` from the chat box.

    The node events go out exactly as the engine emits them, so the topology
    chart animates from the same trace poll that animates a normal turn — a
    named workflow lights the picture as readily as a routed one.
    """
    name, arg = command
    start = datetime.now(UTC)
    if name in ("graphs", "help", "?"):
        emit("done", {"reply": commands.describe(), "tools": [], "iterations": 0,
                      "latency_ms": 0, "gate": None})
        return
    try:
        state = commands.run(name, emit, arg)
    except Exception as exc:
        emit("done", {"reply": f"`/{name}` failed: {type(exc).__name__}: {exc}",
                      "tools": [], "iterations": 0, "latency_ms": 0, "gate": None})
        return
    if state is None:
        emit("done", {"reply": commands.unknown_reply(name), "tools": [],
                      "iterations": 0, "latency_ms": 0, "gate": None})
        return
    reply = state.get("digest") or "(the workflow produced no text)"
    if state.get("ignored_argument"):
        reply = (f"*`/{name}` takes no input, so \u201c{state['ignored_argument']}\u201d "
                 f"was not used — a fixed shape always fetches the same sources. "
                 f"Ask a normal question to use the loop instead.*\n\n") + reply
    if state.get("draft_path"):
        reply += f"\n\n*saved to `{state['draft_path']}`*"
    for node, err in (state.get("errors") or {}).items():
        reply += f"\n\n*{node}: {err}*"
    emit("done", {
        "reply": reply, "tools": [], "gate": None, "consolidation": None,
        "iterations": 0,
        "latency_ms": int((datetime.now(UTC) - start).total_seconds() * 1000),
        "workflow": name,
    })


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _tool_status(output: str) -> str:
    """Classify a tool result for the UI: ok / warn / error — from the output
    string alone (tools already report honestly, so trust their words)."""
    low = (output or "").lower()
    if "failed" in low or "timed out" in low or low.startswith("error"):
        return "error"
    if "already exists" in low or "not synced" in low or "skipped" in low:
        return "warn"
    return "ok"


def collect() -> dict:
    """Everything the page shows, in one JSON blob."""
    settings = load_settings()
    info = settings_info()
    settings.ensure_home()
    home = settings.home
    conn = connect(home)

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql).fetchall()]

    def episodes_payload() -> dict:
        return {
            "source": "sqlite",
            "error": "",
            "items": rows("SELECT id, happened_at, summary FROM episodes ORDER BY happened_at DESC"),
        }

    episodes_data = episodes_payload()

    # --- traces → turns (group events between turn_start and turn_end)
    events = []
    trace_errors = []
    trace_files = sorted((home / "traces").glob("*.jsonl"))
    for path in trace_files:
        try:
            lines = list(iter_trace_lines(path))
        except TraceEncodingError as exc:
            trace_errors.append({"file": path.name, "error": str(exc)})
            continue
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    turns, current = [], None
    for ev in events:
        kind = ev.get("type")
        if kind == "turn_start":
            current = {"user_message": ev.get("user_message"), "ts": ev.get("ts"),
                       "gate": None, "llm_calls": [], "tools": [], "reply": None}
        elif current is not None:
            if kind == "gate":
                current["gate"] = ev
            elif kind == "route":
                current["graph"] = {"workflow": ev.get("workflow"),
                                    "route": "quick" if ev.get("target") == "quick_reply" else "full",
                                    "reason": (current.get("graph") or {}).get("reason", "")}
            elif kind == "triage":
                current.setdefault("graph", {})["reason"] = ev.get("reason", "")
            elif kind == "llm":
                current["llm_calls"].append(ev)
            elif kind == "tool":
                current["tools"].append(ev)
            elif kind == "consolidation":
                current["consolidation"] = ev
            elif kind == "turn_end":
                current["reply"] = ev.get("reply")
                current["iterations"] = ev.get("iterations")
                turns.append(current)
                current = None
    if current is not None:  # a turn that never ended = the smoking gun for hangs
        current["reply"] = "TURN NEVER FINISHED — check for a hang after this point"
        current["unfinished"] = True
        turns.append(current)

    # --- derive per-turn latency + dollar cost (the ops numbers humans feel)
    if settings.base_url:
        list_models()  # warm the per-model price cache (5-min cached fetch)
    price_in, price_out = price_for(settings.provider, settings.model or "")
    for t in turns:
        start, end = _parse_ts(t["ts"]), None
        last = t["llm_calls"][-1]["ts"] if t["llm_calls"] else None
        end = _parse_ts(last)
        t["latency_ms"] = int((end - start).total_seconds() * 1000) if start and end else None
        tin = sum(c.get("usage", {}).get("in", 0) for c in t["llm_calls"])
        tout = sum(c.get("usage", {}).get("out", 0) for c in t["llm_calls"])
        t["cost"] = tin / 1e6 * price_in + tout / 1e6 * price_out
        for x in t["tools"]:
            x["status"] = _tool_status(x.get("output", ""))
            x["summary"] = (x.get("output", "") or "").split(". ")[0][:120]

    latencies = sorted(t["latency_ms"] for t in turns if t["latency_ms"] is not None)
    total_cost = sum(t["cost"] for t in turns)

    def pct(p: float) -> int:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else 0

    from pu.memory import bundled_skill_dirs
    from pu.memory.procedural.loader import SkillLoader

    skills = [{"name": s.name, "description": s.description, "body": s.body,
               "path": str(s.path),
               # relative path (for reveal) + whether it lives in the editable home dir
               "rel": _rel_to_home(s.path, home),
               "editable": str((home / "skills").resolve()) in str(s.path.resolve())}
              for s in SkillLoader([*bundled_skill_dirs(), home / "skills"]).skills]

    eval_report = None
    report_path = home / "eval_report.json"
    if report_path.exists():
        eval_report = json.loads(report_path.read_text(encoding="utf-8"))

    eval_history = []
    hist_path = home / "eval_runs.jsonl"
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                eval_history.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    eval_history.reverse()

    from pu.reporting import FileReportRepository
    reports = FileReportRepository(home).list(limit=50)
    agentops = agentops_info(home, settings)
    workflows = workflows_info(home)

    outbox = [{"name": p.name, "text": p.read_text(encoding="utf-8")[:400]}
              for p in sorted((home / "outbox").glob("*.txt"), reverse=True)[:20]]

    # --- state.db introspection: the actual SQLite tables, so the persistence
    # layer is visible (not just its contents). Table names are hard-coded, so
    # the f-string SQL is safe.
    def table_info(name):
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()
        cols = [r["name"] for r in info]
        types = {r["name"]: r["type"] for r in info}
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        # up to 200 newest rows so each table has its own scrollable tab
        sample = [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY rowid DESC LIMIT 200").fetchall()]
        return {"name": name, "columns": cols, "types": types, "count": count, "sample": sample}

    db_path = home / "state.db"
    all_tables = [r["name"] for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    db_info = {
        "path": str(db_path.resolve()),
        "size": db_path.stat().st_size if db_path.exists() else 0,
        "tables": [table_info(n) for n in (
            "calendar_events", "facts", "episodes", "chat_log", "crm_emails",
            "proposed_actions", "workflow_runs", "workflow_stage_checkpoints",
            "agent_events", "tool_audit_events", "schema_migrations",
        ) if n in all_tables],
        "fts": [t for t in all_tables if t.endswith("_fts")],
        "all_tables": all_tables,
    }

    # Peek at the shared agent WITHOUT building one — a page load should never
    # pay for an agent nobody has chatted with yet.
    live = browser_agent.current()

    # --- graph workflows: topology straight from the engine (never hand-drawn,
    # so the picture can't drift) + quick/full split from the trace events
    from pu.graph.workflows.triage import triage_topology
    graph_routes = [e.get("target") for e in events if e.get("type") == "route"]
    # The last few completed graph runs, newest first. The trace is authoritative
    # for which workflow shape actually ran; the UI should not guess from static
    # topology alone.
    graph_runs = [{"workflow": e.get("workflow"), "ms": e.get("ms"),
                   "at": e.get("ts"), "steps": e.get("steps"),
                   "path": e.get("path") or [], "error": e.get("error")}
                  for e in events if e.get("type") == "graph_end"][-8:][::-1]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home": str(home.resolve()),
        "data_epoch": (home / "reset.marker").read_text(encoding="utf-8").strip() if (home / "reset.marker").is_file() else "",
        "provider": settings.provider,
        "model": info["model"],
        "stats": {
            "turns": len(turns),
            "tool_calls": sum(len(t["tools"]) for t in turns),
            "tool_errors": sum(1 for t in turns for x in t["tools"] if x["status"] == "error"),
            "gate_skips": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "skip"),
            "gate_retrieves": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "retrieve"),
            "tokens_in": sum(c.get("usage", {}).get("in", 0) for t in turns for c in t["llm_calls"]),
            "tokens_out": sum(c.get("usage", {}).get("out", 0) for t in turns for c in t["llm_calls"]),
            "cost": round(total_cost, 4),
            "latency_avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "latency_p95": pct(0.95),
            "trace_files": len(trace_files),
        },
        "turns": turns[::-1][:50],
        # last raw trace lines, so Ops shows traces inline (no folder needed)
        "trace_tail": [{"type": e.get("type"), "ts": e.get("ts"),
                "provider": e.get("provider"), "model": e.get("model"),
                "usage": e.get("usage"),
                "detail": (e.get("user_message") or e.get("decision") or e.get("tool")
                       or e.get("reply") or "")}
                       for e in events[-200:]][::-1],
        "trace_file": (trace_files[-1].name if trace_files else None),
        "trace_errors": trace_errors,
        "facts": rows("SELECT id, subject, content, source, created_at FROM facts ORDER BY id DESC"),
        "episodes": episodes_data["items"],
        "episodes_source": episodes_data["source"],
        "episodes_error": episodes_data["error"],
        "soul": (home / "SOUL.md").read_text(encoding="utf-8") if (home / "SOUL.md").exists() else "",
        "chat_pending": conn.execute("SELECT COUNT(*) FROM chat_log WHERE consolidated=0").fetchone()[0],
        "chat_log": rows("SELECT role, content, consolidated, source, session_id, created_at FROM chat_log ORDER BY id DESC LIMIT 80")[::-1],
        "sessions": session_list(conn),
        "current_session": (live.session.session_id if live is not None else dash_session()),
        "consolidate_every": settings.consolidate_every,
        "calendar": rows('SELECT title, start, "end", attendees, created_at FROM calendar_events ORDER BY start'),
        # CRM inbox: what has been pulled so far (gmail.py syncs on demand, so
        # this is the local store, never a live Gmail call on page load).
        "crm": rows("SELECT gmail_id, sender_name, sender_email, subject, snippet, "
                    "internal_date, unread FROM crm_emails ORDER BY internal_date DESC LIMIT 100"),
        "gmail_enabled": settings.gmail,
        "gmail_connected": (home / "gmail-token.json").exists(),
        "knowledge_sources": [dict(r) for r in conn.execute(
            """SELECT id, title, "group", origin, path, enabled, chars, added_at
               FROM knowledge_sources ORDER BY "group", title""")],
        "drive_connected": (home / "drive-token.json").exists(),
        "outbox": outbox,
        "skills": skills,
        "eval_report": eval_report,
        "eval_history": eval_history,
        "reports": reports,
        "agentops": agentops,
        "workflows": workflows,
        "graph": {
            "enabled": settings.graph_workflows,
            "workflows": [triage_topology()],
            "runs": graph_runs,
            "stats": {"quick": sum(1 for t in graph_routes if t == "quick_reply"),
                      "full": sum(1 for t in graph_routes if t == "full_agent")},
        },
        "db": db_info,
        "settings": info,
        "providers": [asdict(view) for view in list_providers()],
        "connections": [asdict(view) for view in list_connections()],
        "tools": tools_info(),
        "agents": agents_info(),
        "approvals": approvals_info(home),
        "security": security_info(home),
        "usage": usage_summary(home),
    }


def _rel_to_home(path, home) -> str:
    """Path relative to PU_HOME if it lives there, else the repo-relative
    'skills/...' path — either way something reveal_path can open."""
    try:
        return str(path.resolve().relative_to(home.resolve()))
    except ValueError:
        return str(path)


def session_list(conn) -> list[dict]:
    """One row per conversation for the chat-history picker: id, its first user
    message (the title), message count, newest first. Sessions are just a
    session_id label on chat_log rows — the same table, no new storage."""
    groups = conn.execute(
        """SELECT session_id, COUNT(*) AS messages, MAX(created_at) AS last_at
           FROM chat_log GROUP BY session_id ORDER BY last_at DESC"""
    ).fetchall()
    out = []
    for g in groups:
        sid = g["session_id"]
        first = conn.execute(
            "SELECT content FROM chat_log WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (sid,),
        ).fetchone()
        last = conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)
        ).fetchone()
        sources = [r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM chat_log WHERE session_id=?", (sid,)).fetchall()]
        preview = ""
        if last:
            preview = ("you: " if last["role"] == "user" else "pu: ") + last["content"][:80]
        out.append({"id": sid,
                    "title": (first["content"][:60] if first else "(empty)"),
                    "last": preview,
                    "sources": sources,
                    "messages": g["messages"],
                    "last_at": g["last_at"]})
    return out


# A tool's origin, for grouping in the Tools tab.
_FLAGSHIP = {"create_event", "list_events", "save_note", "send_message"}
_SELFMGMT = {"manage_memory", "update_soul", "create_skill"}
_WEB = {"search_web"}
_EMAIL = {"read_email_inbox", "read_gmail_today"}
_KNOWLEDGE = {"search_knowledge", "search_memory"}


def _tool_source(name: str) -> str:
    if name in _FLAGSHIP:
        return "flagship"
    if name in _WEB:
        return "web"
    if name in _EMAIL:
        return "email"
    if name in _KNOWLEDGE:
        return "knowledge"
    if name in _SELFMGMT:
        return "self-management"
    return "other"


def tools_info() -> dict:
    """Return the product-scope capabilities available to the current agent."""
    settings = load_settings()
    settings.ensure_home()
    live = browser_agent.current()
    if live is not None:
        tools = list(live.tools._tools.values())
    else:
        from pu.memory import Memory
        from pu.tools import (
            calendar,
            gmail,
            knowledge_search,
            memory_admin,
            memory_search,
            messages,
            notes,
            search,
        )

        conn = connect(settings.home)
        mem = Memory(conn, settings, None)
        tools = [
            calendar.make_tool(conn, settings.home,
                               google_calendar=settings.google_calendar,
                               google_calendar_id=settings.google_calendar_id),
            calendar.make_list_tool(conn, settings.home),
            notes.make_tool(conn),
            messages.make_tool(settings.home),
            search.make_tool(),
            gmail.make_local_tool(conn),
            knowledge_search.make_tool(conn),
            memory_search.make_tool(mem),
            memory_admin.make_update_soul_tool(settings),
            memory_admin.make_manage_memory_tool(mem),
            memory_admin.make_create_skill_tool(settings, mem),
        ]
        if settings.gmail:
            from pu.tools import gmail
            tools.append(gmail.make_tool(conn, settings.home))

    catalog = [{
        "name": t.name,
        "description": t.description,
        "source": _tool_source(t.name),
        "capability": t.definition.capability,
        "agent_name": f"propose_{t.name}" if t.definition.side_effect else t.name,
        "agent_capability": "action:propose" if t.definition.side_effect else t.definition.capability,
        "scope": t.definition.required_scope,
        "side_effect": t.definition.side_effect,
        "external": t.definition.external,
    } for t in tools]
    catalog.sort(key=lambda c: (c["source"], c["name"]))
    return {"catalog": catalog}



def agents_info() -> dict:
    """Inspect specialized agents without constructing an LLM on page load."""
    live = browser_agent.current()
    if live is None:
        from pu.agents.catalog import AGENT_IDENTITIES, AGENT_TOOL_CATALOG
        items = []
        for key in ("email", "company", "web", "calendar", "memory", "report"):
            agent_id, name = AGENT_IDENTITIES[key]
            tools = list(AGENT_TOOL_CATALOG[key])
            if key == "email" and not load_settings().gmail:
                tools = [tool for tool in tools if tool != "read_gmail_today"]
            items.append({"agent_id": agent_id, "name": name, "tools": tools, "read_only": True})
        return {"mode": "specialized-read-only", "workflow": "morning_research", "items": items}
    return {
        "mode": "specialized-read-only",
        "workflow": "morning_research",
        "items": live.agent_orchestrator.descriptors(),
    }


def security_info(home: Path) -> dict:
    """Summarize the enforced boundary from the durable SQL audit ledger."""
    database = SQLiteDatabase.from_home(home)
    records = SQLiteToolAuditSink(database).list(limit=250)
    blocked = [r for r in records if r.get("status") == "blocked"]
    policies = {}
    for row in blocked:
        key = row.get("policy") or "unspecified"
        policies[key] = policies.get(key, 0) + 1
    return {
        "enforced": True, "approval_required": True,
        "audit_path": str((home / "state.db").resolve()) + "::tool_audit_events",
        "events": len(records), "blocked": len(blocked), "policies": policies,
        "recent": records[:40],
    }


def agentops_info(home: Path, settings=None) -> dict:
    settings = settings or load_settings()
    repo = SQLiteAgentEventRepository(SQLiteDatabase.from_home(home))
    metrics = repo.aggregate()
    slo = SLOEvaluator(settings).evaluate(metrics)
    return {"metrics": metrics, "slo": slo, "recent": repo.list(limit=80)}


def workflows_info(home: Path) -> dict:
    repo = SQLiteWorkflowRepository(SQLiteDatabase.from_home(home))
    runs = repo.list(limit=50)
    return {
        "items": [asdict(run) for run in runs],
        "recoverable": sum(1 for run in runs if run.status in {"failed", "running", "interrupted"}),
        "completed": sum(1 for run in runs if run.status == "completed"),
    }


def prometheus_snapshot() -> str:
    settings = load_settings(); settings.ensure_home()
    # /metrics may be the first request after startup; ensure migrations exist.
    connect(settings.home).close()
    repo = SQLiteAgentEventRepository(SQLiteDatabase.from_home(settings.home))
    metrics = repo.aggregate()
    return prometheus_text(metrics, SLOEvaluator(settings).evaluate(metrics))

def _approval_payload(action) -> dict:
    data = asdict(action)
    data["status"] = action.status.value
    return data


def approvals_info(home: Path) -> dict:
    """Durable Human Approval queue; page loads never build an LLM client."""
    from pu.approval import ApprovalStatus, HumanApprovalService, SQLiteApprovalRepository
    from pu.persistence import SQLiteDatabase

    service = HumanApprovalService(
        SQLiteApprovalRepository(SQLiteDatabase.from_home(home)),
        ttl_minutes=load_settings().approval_ttl_minutes,
    )
    items = service.list(limit=200)
    counts = {status.value: 0 for status in ApprovalStatus}
    for item in items:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return {
        "items": [_approval_payload(item) for item in items],
        "counts": counts,
        "pending": counts.get("pending", 0),
    }


def research_action(payload: dict) -> dict:
    """Run the deterministic read-only multi-agent research workflow."""
    prompt = (payload.get("prompt") or "").strip() or None
    try:
        days = max(1, min(int(payload.get("email_days") or 7), 30))
        limit = max(1, min(int(payload.get("max_emails") or 20), 100))
    except (TypeError, ValueError):
        return {"error": "email_days/max_emails must be integers"}
    events = []
    def observer(kind, ev):
        if kind.startswith("workflow_"):
            events.append({"kind": kind, **ev})
    from pu.orchestration.research import WorkflowExecutionError
    try:
        with agent_lock:
            agent = get_agent()
            if payload.get("op") == "resume":
                workflow_id = (payload.get("workflow_id") or "").strip()
                if not workflow_id:
                    return {"error": "workflow_id is required for resume"}
                result = agent.resume_research_workflow(workflow_id, observer=observer)
            else:
                result = agent.run_research_workflow(prompt, observer=observer, email_days=days, max_emails=limit)
    except WorkflowExecutionError as exc:
        return {
            "workflow_id": exc.workflow_id, "status": "failed", "stage": exc.stage,
            "recoverable": True, "error": str(exc), "events": events,
        }
    return {
        "workflow_id": result.workflow_id,
        "status": result.status,
        "report": result.report.reply if result.report else "",
        "structured_report": result.structured_report.to_dict() if result.structured_report else None,
        "evaluation": result.evaluation.to_dict() if result.evaluation else None,
        "stages": {name: stage.reply for name, stage in result.stages.items()},
        "events": events,
    }


def approval_action(payload: dict) -> dict:
    """Trusted local UI/API approval decision; never calls an external API directly."""
    op = (payload.get("op") or "").strip()
    action_id = (payload.get("id") or "").strip()
    if not action_id:
        return {"error": "approval id is required"}

    # All decisions + execution are serialized with chat turns because the live
    # application owns one cross-thread SQLite connection and one ToolHarness.
    with agent_lock:
        agent = get_agent()
        service = agent.approval_service
        actor = "local-dashboard-user"
        if op == "edit":
            arguments = payload.get("arguments")
            if not isinstance(arguments, dict):
                return {"error": "edited arguments must be a JSON object"}
            action = service.get(action_id)
            if action is None:
                return {"error": "approval not found"}
            tool = agent.tools.resolve(action.tool_name)
            from pu.harness.tools import ToolValidationError, validate_arguments
            try:
                arguments = validate_arguments(tool.definition.input_schema, arguments)
            except ToolValidationError as exc:
                return {"error": f"invalid edited arguments: {exc}"}
            action = service.edit(action_id, arguments)
            agent.event_publisher.publish("approval_edited", {
                "approval_id": action.id, "action_hash": action.action_hash,
                "revision": action.revision, "actor": actor,
            })
            return {"action": _approval_payload(action)}
        if op == "reject":
            action = service.reject(action_id, note=(payload.get("note") or ""), decided_by=actor)
            agent.event_publisher.publish("approval_rejected", {
                "approval_id": action.id, "action_hash": action.action_hash, "actor": actor,
            })
            return {"action": _approval_payload(action)}
        if op == "approve_execute":
            action = service.approve(action_id, note=(payload.get("note") or ""), decided_by=actor)
            agent.event_publisher.publish("approval_approved", {
                "approval_id": action.id, "action_hash": action.action_hash, "actor": actor,
            })
            final, result = agent.approved_action_executor.execute(
                action.id, observer=agent.event_publisher.publish
            )
            return {
                "action": _approval_payload(final),
                "result": {
                    "ok": result.ok, "output": result.output, "error": result.error,
                    "metadata": result.metadata,
                },
            }
        return {"error": f"unknown approval op '{op}'"}


def gmail_sync_action(payload: dict) -> dict:
    """POST /api/gmail/sync — pull read-only from Gmail into crm_emails.

    Sync is always on demand (a click here or a tool call), so this endpoint
    IS the dashboard's trigger. The button must never 500 the page: sync()
    already reports every failure as a sentence."""
    from pu.tools import gmail

    settings = load_settings()
    home = settings.ensure_home()
    label = (payload.get("label") or "").strip() or settings.gmail_label
    try:
        max_messages = int(payload.get("max_messages") or settings.gmail_max_messages)
    except (TypeError, ValueError):
        max_messages = settings.gmail_max_messages
    conn = connect(home)
    try:
        return {"message": gmail.sync(conn, home, label=label, max_messages=max_messages)}
    finally:
        conn.close()


def gmail_connect_action(payload: dict) -> dict:
    """POST /api/gmail/connect — open the browser for Google OAuth consent.

    Same rule as google_calendar.connect: this is ONE of the two allowed
    consent entry points (the other is `pu connect gmail`), never mid-turn."""
    from pu.tools import gmail

    home = load_settings().ensure_home()
    return {"message": gmail.connect(home)}


def knowledge_action(payload: dict) -> dict:
    """POST /api/knowledge — every Knowledge-tab operation, dispatched on op.

    One route keeps the JS side to one postJSON helper, the way /api/memory
    already works. Upload is NOT multipart: the browser reads the text file
    with FileReader and posts its content as JSON (text-only formats by
    design), so this stdlib server never parses multipart bodies."""
    from pu.memory import knowledge as kb

    home = load_settings().ensure_home()
    conn = connect(home)
    try:
        op = payload.get("op", "")
        if op == "add":
            title = (payload.get("title") or "").strip() or "Untitled source"
            text = payload.get("text") or ""
            if not text.strip():
                return {"error": "Source text is empty — nothing to store."}
            sid = kb.add_source(conn, title, text,
                                group=payload.get("group") or "",
                                origin=payload.get("origin") or "paste")
            return {"message": f"Added '{title}'.", "id": sid}
        if op == "search":
            return {"results": kb.search(conn, payload.get("query") or "", top_k=8)}
        if op == "toggle":
            kb.set_enabled(conn, int(payload.get("id", 0)), bool(payload.get("enabled")))
            return {"message": "Updated."}
        if op == "group":
            kb.set_group(conn, int(payload.get("id", 0)), payload.get("group") or "")
            return {"message": "Group updated."}
        if op == "delete":
            kb.delete_source(conn, int(payload.get("id", 0)))
            return {"message": "Deleted."}
        if op == "source":  # View: the chunks behind one source
            src = kb.get_source(conn, int(payload.get("id", 0)))
            return {"source": src, "chunks": kb.chunks_for(conn, int(payload.get("id", 0)))}
        if op == "reindex":
            return {"message": kb.reindex_folder(conn, home / "knowledge")}
        return {"error": f"Unknown knowledge op '{op}'"}
    finally:
        conn.close()


def drive_action(payload: dict) -> dict:
    """POST /api/drive — connect / list / import for the Knowledge dialog.

    connect() may open the consent browser: an allowed entry point (like the
    Gmail card), because the user clicked it on purpose."""
    from pu.tools import google_drive

    home = load_settings().ensure_home()
    op = payload.get("op", "")
    if op == "connect":
        return {"message": google_drive.connect(home)}
    conn = connect(home)
    try:
        if op == "list":
            files = google_drive.list_files(home)  # RuntimeError = setup hint
            return {"files": files}
        if op == "import":
            return {"message": google_drive.import_file(
                conn, home, payload.get("file_id", ""))}
        return {"error": f"Unknown drive op '{op}'"}
    except RuntimeError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def run_query(payload: dict) -> dict:
    """A tiny read-only SQL console.
    Opens state.db in read-only mode so a write can't slip through, and only
    accepts a single SELECT/WITH statement. Caps at 200 rows."""
    sql = (payload.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return {"error": "Type a SELECT query."}
    low = sql.lower()
    if not (low.startswith(("select", "with"))):
        return {"error": "Only SELECT (or WITH … SELECT) queries are allowed."}
    if ";" in sql:
        return {"error": "One statement at a time (no semicolons)."}
    import sqlite3

    settings = load_settings()
    settings.ensure_home()
    db = (settings.home / "state.db").resolve()
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = [[str(r[i]) if r[i] is not None else "" for i in range(len(cols))]
                for r in cur.fetchmany(200)]
        c.close()
        return {"columns": cols, "rows": data}
    except sqlite3.Error as exc:
        return {"error": str(exc)}


_whisper = None
_whisper_lock = threading.Lock()


def _thread_history(conn, sid: str) -> list[dict]:
    """The ONE way to load a thread for the chat dock: role + content + the
    per-turn meta (gate/stats/tools/model) so every card renders in full.
    id '__all__' returns the whole cross-thread timeline (like the Loop tab,
    but as chat). Every history-loading path goes through here so they can't
    drift apart (they used to: 'switch' dropped meta and showed only text)."""
    if sid == "__all__":
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log ORDER BY id DESC LIMIT 200"
        ).fetchall()[::-1]
    else:
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log WHERE session_id=? ORDER BY id",
            (sid,),
        ).fetchall()
    # History written before usage metadata existed can still recover its token
    # counts from traces by matching each completed turn's user message.
    traced = {}
    current = None
    for path in sorted((load_settings().home / "traces").glob("*.jsonl")):
        try:
            events = (json.loads(line) for line in iter_trace_lines(path))
            for event in events:
                if event.get("type") == "turn_start":
                    current = {"message": event.get("user_message"),
                               "provider": None, "model": None,
                               "usage": {"in": 0, "out": 0, "calls": 0}}
                elif current is not None and event.get("type") == "llm":
                    usage = event.get("usage") or {}
                    current["provider"] = event.get("provider") or current["provider"]
                    current["model"] = event.get("model") or current["model"]
                    current["usage"]["in"] += int(usage.get("in", 0) or 0)
                    current["usage"]["out"] += int(usage.get("out", 0) or 0)
                    current["usage"]["calls"] += 1
                elif current is not None and event.get("type") == "turn_end":
                    traced.setdefault(current["message"], []).append(current)
                    current = None
        except TraceEncodingError:
            continue

    out, pending_user = [], None
    for row in rows:
        meta = json.loads(row["meta"]) if row["meta"] else None
        if row["role"] == "user":
            pending_user = row["content"]
        elif meta is None or not meta.get("usage"):
            matches = traced.get(pending_user or "", [])
            if matches:
                trace = matches.pop(0)
                meta = dict(meta or {})
                meta["usage"] = trace["usage"]
                meta.setdefault("provider", trace["provider"])
                meta.setdefault("model", trace["model"])
        out.append({"role": row["role"], "content": row["content"], "meta": meta})
    return out


def session_action(payload: dict) -> dict:
    """Chat history control: start a new conversation, switch to a past one, or
    read a conversation's history (read-only, for the live inbox). Sessions live
    in chat_log."""
    action = payload.get("action")
    if action == "history":
        # read-only view of a conversation — never touches the agent, so the
        # dashboard can poll it live without rebuilding the agent.
        settings = load_settings()
        settings.ensure_home()
        conn = connect(settings.home)
        sid = payload.get("id") or "default"
        return {"ok": True, "session_id": sid, "history": _thread_history(conn, sid)}
    with agent_lock:
        agent = get_agent()
        if action == "new":
            sid = datetime.now().strftime("s-%Y%m%d-%H%M%S")
            agent.session.start_new(sid)
            return {"ok": True, "session_id": sid, "history": []}
        if action == "switch":
            sid = payload.get("id") or "default"
            agent.session.switch(sid)
            # Same meta-rich rows as the read-only "history" action, so a
            # switched thread renders its full turn cards (gate/stats/tools/
            # model) — not just the text. (These two paths used to disagree.)
            return {"ok": True, "session_id": sid, "history": _thread_history(agent.conn, sid)}
    return {"error": f"unknown action {action}"}


def _editor_cmd() -> list[str] | None:
    """The user's code editor CLI: $PU_EDITOR, then cursor, then code."""

    custom = os.getenv("PU_EDITOR")
    if custom and shutil.which(custom):
        return [custom]
    for cli in ("cursor", "code"):
        if shutil.which(cli):
            return [cli]
    return None


def reveal_path(rel: str) -> dict:
    """Open a file/folder under PU_HOME — in the user's code editor if one
    is on PATH (cursor/code/$PU_EDITOR), otherwise reveal in Finder.
    Restricted to paths inside PU_HOME."""
    import subprocess
    import sys

    settings = load_settings()
    settings.ensure_home()
    home = settings.home.resolve()
    target = (home / (rel or ".")).resolve()
    if target != home and home not in target.parents:
        return {"error": "path is outside the .pu home"}
    if not target.exists():
        return {"error": f"not found: {target}"}

    editor = _editor_cmd()
    if editor and target.is_file() and target.suffix != ".db":  # editors choke on sqlite
        subprocess.run([*editor, str(target)], check=False)
        return {"ok": True, "opened_in": editor[0], "path": str(target)}
    if sys.platform != "darwin":
        return {"error": f"no editor found and reveal is macOS-only — the path is {target}"}
    subprocess.run(
        ["open", "-R", str(target)] if target.is_file() else ["open", str(target)],
        check=False,
    )
    return {"ok": True, "revealed": str(target)}


def memory_action(payload: dict) -> dict:
    """Human CRUD on memory from the dashboard: update/delete facts & episodes,
    rewrite SOUL.md. Writes the same sqlite file the agent uses (busy_timeout
    covers contention); changes are live for the next agent turn."""
    from pu.memory.episodic.store import SqliteEpisodeStore
    from pu.memory.semantic.store import SqliteFactStore

    settings = load_settings()
    settings.ensure_home()
    action = payload.get("action")
    if action == "save_soul":
        text = (payload.get("content") or "").strip()
        if not text:
            return {"error": "SOUL cannot be empty"}
        (settings.home / "SOUL.md").write_text(text + "\n")
        return {"ok": True}
    if action == "save_skill":
        # Edit any loaded SKILL.md by hand (same file the agent's create_skill
        # writes) — repo skills and home skills alike. Sandboxed to the two
        # skills folders; validates the frontmatter before writing.
        from pathlib import Path

        from pu.memory import bundled_skill_dirs
        from pu.memory.procedural.loader import _parse_text

        text = (payload.get("content") or "").strip()
        dest = Path(payload.get("path") or "").resolve()
        allowed = [d.resolve() for d in bundled_skill_dirs()] + [(settings.home / "skills").resolve()]
        if dest.name != "SKILL.md" or not any(a in dest.parents for a in allowed):
            return {"error": "can only edit SKILL.md files inside the skills folders"}
        if _parse_text(text, dest) is None:
            return {"error": "invalid SKILL.md — needs a name and description in the frontmatter"}
        dest.write_text(text.rstrip() + "\n", encoding="utf-8")
        return {"ok": True}

    conn = connect(settings.home)
    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    try:
        rid = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return {"error": "bad id"}
    if action == "update_fact":
        return {"ok": facts.update(rid, payload.get("content", ""), payload.get("subject") or None)}
    if action == "delete_fact":
        return {"ok": facts.delete(rid)}
    if action == "delete_episode":
        return {"ok": episodes.delete(rid)}
    return {"error": f"unknown action {action}"}




def events_since(cursor):
    """New trace events past `cursor` (a line count in today's trace file).
    Both supported interactive surfaces — browser and CLI — append to this same file,
    so the live diagram lights up for all of them. cursor=None returns just
    the current tail so the browser starts fresh instead of replaying history."""
    settings = load_settings()
    settings.ensure_home()
    path = settings.home / "traces" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    if not path.exists():
        return {"events": [], "cursor": 0}
    try:
        lines = list(iter_trace_lines(path))
    except TraceEncodingError as exc:
        return {"events": [], "cursor": 0, "error": str(exc)}
    if cursor is None or cursor < 0 or cursor > len(lines):
        return {"events": [], "cursor": len(lines)}
    out = []
    for ln in lines[cursor:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return {"events": out, "cursor": len(lines)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, *, no_cache: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The frontend files (app.js/style.css) change as we develop; without
        # this the browser serves a stale cached copy and edits look "missing".
        if no_cache:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/briefing":
            from pu.ops.briefing import briefing_service
            settings = load_settings()
            settings.ensure_home()
            self._send(json.dumps(briefing_service(settings.home).snapshot(), ensure_ascii=False).encode(), "application/json", no_cache=True)
        elif self.path.startswith("/api/workspaces") or self.path.startswith("/api/workspace/export"):
            from urllib.parse import parse_qs, urlparse

            from pu.ops.workspace_api import workspace_list
            params = parse_qs(urlparse(self.path).query)
            settings = load_settings()
            settings.ensure_home()
            try:
                if self.path.startswith("/api/workspace/export"):
                    from pu.reporting.workspace import WorkspaceRepository
                    repo = WorkspaceRepository(settings.home)
                    stored = repo.require_report(params.get("id", [""])[0], params.get("report", [""])[0])
                    kind = params.get("format", ["md"])[0]
                    report_id = stored["payload"]["report_id"]
                    if kind == "md":
                        data, content_type = stored["payload"]["markdown"].encode(), "text/markdown; charset=utf-8"
                    elif kind == "json":
                        data, content_type = json.dumps(stored["payload"], ensure_ascii=False, indent=2).encode(), "application/json"
                    elif kind == "docx":
                        from pu.reporting.docx import render_docx
                        data, content_type = render_docx(stored["payload"]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif kind in {"html", "pdf"}:
                        artifact = settings.home / "reports" / f"{report_id}.{kind}"
                        original = settings.home / "reports" / f"{report_id}.json"
                        from pu.reporting.workspace import content_hash
                        if not artifact.is_file() or not original.is_file() or content_hash(json.loads(original.read_text(encoding="utf-8"))) != stored["content_hash"]:
                            raise ValueError("Artifact chưa có hoặc không khớp phiên bản. Có thể xuất Markdown/JSON.")
                        data, content_type = artifact.read_bytes(), "application/pdf" if kind == "pdf" else "text/html; charset=utf-8"
                    else:
                        raise ValueError("Định dạng không hỗ trợ.")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Disposition", f'attachment; filename="{report_id}.{kind}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    out = workspace_list(settings.home, params.get("id", [""])[0], params.get("report", [""])[0])
                    self._send(json.dumps(out, default=str).encode(), "application/json", no_cache=True)
            except (ValueError, KeyError, OSError) as exc:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode())
        elif self.path == "/metrics":
            self._send(prometheus_snapshot().encode(), "text/plain; version=0.0.4; charset=utf-8", no_cache=True)
        elif self.path == "/api/data":
            self._send(json.dumps(collect(), default=str).encode(), "application/json")
        elif self.path.startswith("/api/models"):
            from urllib.parse import parse_qs, urlparse

            prov = parse_qs(urlparse(self.path).query).get("provider", [None])[0]
            self._send(json.dumps(list_models(prov)).encode(), "application/json")
        elif self.path.startswith("/api/events"):
            from urllib.parse import parse_qs, urlparse

            raw = parse_qs(urlparse(self.path).query).get("cursor", [None])[0]
            cursor = int(raw) if raw and raw.lstrip("-").isdigit() else None
            self._send(json.dumps(events_since(cursor)).encode(), "application/json")
        elif self.path.startswith("/api/reveal"):
            from urllib.parse import parse_qs, unquote, urlparse

            rel = unquote(parse_qs(urlparse(self.path).query).get("path", [""])[0])
            self._send(json.dumps(reveal_path(rel)).encode(), "application/json")
        elif self.path.startswith("/static/"):
            self._serve_static(self.path)
        else:
            self._send((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, path: str) -> None:  # the frontend files
        name = path.split("/static/", 1)[1].split("?")[0]
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ctype = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
                 ".html": "text/html; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype, no_cache=True)

    def do_POST(self):
        from urllib.parse import urlparse
        origin = self.headers.get("Origin")
        if (origin and (urlparse(origin).netloc != self.headers.get("Host") or urlparse(origin).scheme != "http")) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_response(403)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 2_000_000:
                raise ValueError("Request body vượt giới hạn 2 MB.")
        except ValueError:
            self.send_response(413)
            self.end_headers()
            return
        if self.path in {"/api/workspace", "/api/briefing"}:
            from pu.ops.workspace_api import workspace_action
            try:
                payload = json.loads(self.rfile.read(length) or "{}")
                if not isinstance(payload, dict):
                    raise TypeError("Request phải là JSON object.")
                settings = load_settings()
                settings.ensure_home()
                if self.path == "/api/briefing":
                    from pu.ops.briefing import briefing_action
                    out = briefing_action(settings.home, payload)
                else:
                    out = workspace_action(settings.home, payload)
                self._send(json.dumps(out, default=str).encode(), "application/json")
            except Exception as exc:
                from pu.harness.audit import redact
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": redact(str(exc))}, ensure_ascii=False).encode())
            return
        # /api/chat/stream streams harness events (SSE) as the turn runs.
        if self.path == "/api/chat/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            message = (payload.get("message") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the browser navigated away mid-stream — fine

            if not message:
                emit("done", {"error": "empty message"})
                return
            try:
                chat_stream(message, emit)
            except Exception as exc:  # surface as a terminal event, don't 500
                emit("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        if self.path == "/api/graph/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            graph_stream(payload, emit)
            return
        routes = {"/api/chat": None, "/api/memory": memory_action, "/api/settings": apply_settings,
                  "/api/query": run_query, "/api/session": session_action, "/api/pin": pin_action,
                  "/api/connections": None, "/api/connections/test": None,
                  "/api/providers": None,
                  "/api/gmail/sync": gmail_sync_action, "/api/gmail/connect": gmail_connect_action,
                  "/api/knowledge": knowledge_action, "/api/drive": drive_action,
                  "/api/research": research_action, "/api/approvals": approval_action}
        if self.path not in routes:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.loads(self.rfile.read(length) or "{}")
        try:
            if self.path == "/api/chat":
                message = (payload.get("message") or "").strip()
                out = chat(message) if message else {"error": "empty message"}
            elif self.path == "/api/connections":
                result = apply_integration(payload.get("key", ""), payload.get("values") or {},
                                           tuple(payload.get("clear") or ()), force=bool(payload.get("force")))
                out = asdict(result)
            elif self.path == "/api/connections/test":
                out = asdict(test_integration(payload.get("key", "")))
            elif self.path == "/api/providers":
                # A payload that only toggles availability goes to the enable/
                # disable path; everything else is the existing provider apply.
                if "disabled" in payload and set(payload) <= {"provider", "disabled"}:
                    out = asdict(apply_provider_disabled(payload.get("provider", ""),
                                                         disabled=bool(payload["disabled"])))
                else:
                    out = asdict(apply_provider(**payload))
            else:
                out = routes[self.path](payload)
        except Exception as exc:  # surface, don't 500 — the browser shows it
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(json.dumps(out, default=str).encode(), "application/json")

    def log_message(self, *args):  # keep the terminal quiet
        pass


def main() -> None:
    base = int(os.getenv("PU_DASHBOARD_PORT") or os.getenv("PORT") or PORT)
    for port in range(base, base + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            print(f"port {port} busy, trying {port + 1}…")
            continue
        print(f"PU dashboard → http://localhost:{port}  (Ctrl-C to stop)")
        from pu.ops.briefing import briefing_service
        settings = load_settings()
        settings.ensure_home()
        worker = briefing_service(settings.home, start=True)
        try:
            server.serve_forever()
        finally:
            worker.close()
            server.server_close()
        return
    raise SystemExit(f"no free port in {base}–{base + 9}")


if __name__ == "__main__":
    main()
