"""Default product tool catalog.

This module is responsible for registration only. Runtime execution
always crosses ToolHarness + SecurityHarness; ToolRegistry is metadata/catalog.
"""

from __future__ import annotations

import sqlite3

from pu.config import Settings
from pu.tools import calendar, memory_admin, messages, notes, search
from pu.tools.registry import ToolRegistry


def build_registry(conn: sqlite3.Connection, settings: Settings, memory=None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(calendar.make_tool(
        conn,
        settings.home,
        google_calendar=settings.google_calendar,
        google_calendar_id=settings.google_calendar_id,
    ))
    registry.register(calendar.make_list_tool(conn, settings.home))
    registry.register(notes.make_tool(conn))
    registry.register(messages.make_tool(settings.home))
    registry.register(search.make_tool())
    from pu.tools.web_source import make_tool as web_page_tool
    registry.register(web_page_tool())
    from pu.tools.report_delivery import make_tools
    for tool in make_tools(settings.home):
        registry.register(tool)

    from pu.tools import gmail
    registry.register(gmail.make_today_tool(conn, settings.home))
    registry.register(gmail.make_local_tool(conn))

    from pu.reporting.availability import make_week_tool
    registry.register(make_week_tool(settings.home))

    from pu.tools import knowledge_search
    registry.register(knowledge_search.make_tool(conn))

    if memory is not None:
        from pu.tools import memory_search
        registry.register(memory_search.make_tool(memory))
        registry.register(memory_admin.make_manage_memory_tool(memory))
        registry.register(memory_admin.make_update_soul_tool(settings))
        registry.register(memory_admin.make_create_skill_tool(settings, memory))

    if getattr(settings, "gmail", False):
        registry.register(gmail.make_tool(conn, settings.home))

    return registry
