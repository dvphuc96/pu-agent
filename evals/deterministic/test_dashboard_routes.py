"""Characterization net for the Phase-2 dashboard HTTP surface."""

from __future__ import annotations

import inspect

from pu.ops import dashboard

POST_ROUTES = {
    "/api/chat", "/api/memory", "/api/settings", "/api/query", "/api/session",
    "/api/pin", "/api/connections", "/api/connections/test", "/api/providers",
    "/api/gmail/sync", "/api/gmail/connect", "/api/knowledge", "/api/drive",
}
GET_PATHS = {"/api/data", "/api/models", "/api/events", "/api/reveal", "/static/"}
STREAM_ROUTES = {"/api/chat/stream", "/api/graph/stream"}


def _source() -> str:
    return inspect.getsource(dashboard)


def test_every_post_route_is_registered():
    src = _source()
    for path in POST_ROUTES:
        assert f'"{path}"' in src, f"POST route disappeared: {path}"


def test_every_get_path_is_served():
    src = _source()
    for path in GET_PATHS:
        assert f'"{path}"' in src, f"GET path disappeared: {path}"


def test_streaming_surface_contains_only_chat_and_graph():
    src = _source()
    for path in STREAM_ROUTES:
        assert f'"{path}"' in src
    assert "/api/voice" not in src
    assert "/api/compare/stream" not in src


def test_retained_handlers_are_callable():
    for name in (
        "collect", "chat", "chat_stream", "graph_stream", "memory_action",
        "apply_settings", "run_query", "session_action", "pin_action",
        "list_models", "events_since", "reveal_path", "settings_info", "tools_info",
        "gmail_sync_action", "gmail_connect_action", "knowledge_action", "drive_action",
    ):
        assert callable(getattr(dashboard, name, None)), name


def test_collect_returns_keys_the_page_reads():
    expected = {
        "settings", "connections", "tools", "facts", "episodes", "soul",
        "chat_log", "sessions", "turns", "stats", "db", "skills", "trace_file",
        "chat_pending", "graph", "crm", "knowledge_sources",
    }
    src = inspect.getsource(dashboard.collect)
    for key in expected:
        assert f'"{key}"' in src, f"collect() no longer returns: {key}"


def test_removed_benchmark_routes_stay_removed():
    src = _source()
    for path in ("/api/compare", "/api/memory-arena", "/api/voice"):
        assert path not in src
