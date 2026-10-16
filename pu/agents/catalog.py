"""Single source of truth for specialized-agent tool visibility."""
from __future__ import annotations

AGENT_TOOL_CATALOG: dict[str, tuple[str, ...]] = {
    "email": ("read_email_inbox", "read_gmail_today"),
    "company": ("search_knowledge", "search_memory"),
    "web": ("search_web", "read_web_page"),
    "calendar": ("list_events",),
    "memory": ("search_memory",),
    "report": (),
}

AGENT_IDENTITIES: dict[str, tuple[str, str]] = {
    "email": ("email-agent", "Email Agent"),
    "company": ("company-info-agent", "Company Info Agent"),
    "web": ("web-research-agent", "Web Research Agent"),
    "calendar": ("calendar-agent", "Calendar Agent"),
    "memory": ("memory-agent", "Memory Agent"),
    "report": ("report-generation-agent", "Report Generation Agent"),
}
