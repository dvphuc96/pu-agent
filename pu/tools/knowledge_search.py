"""search_knowledge — the agent's door into what the user taught it.

The retrieval gate already injects knowledge automatically when a turn needs
memory; this tool is for DIGGING — "quy trình bán hàng nói gì?", "đọc lại
tài liệu X đi". Always registered (knowledge is core memory, like search_web
is core capability): with nothing stored it says so honestly, which is also
how the model learns the capability exists.
"""

from __future__ import annotations

import sqlite3

from pu.memory import knowledge
from pu.tools.registry import Tool


def make_tool(conn: sqlite3.Connection) -> Tool:
    def search_knowledge(query: str, top_k: int = 5) -> str:
        hits = knowledge.search(conn, query, top_k=top_k)
        if not hits:
            return ("No knowledge matches that. Sources live in the dashboard's "
                    "Memory ▸ Knowledge tab — add documents there first.")
        lines = []
        for hit in hits:
            lines.append(f"[{hit['title']}] {hit['content'][:400]}")
        return "\n\n".join(lines)

    return Tool(
        name="search_knowledge",
        description=(
            "Search the user's knowledge base — documents they added on purpose "
            "(playbooks, notes, specs). Use when the question might be answered "
            "by material the user taught you, especially process/domain questions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "keywords to search for"},
                "top_k": {"type": "integer", "description": "how many chunks to return (default 5)"},
            },
            "required": ["query"],
        },
        fn=search_knowledge,
        capability="knowledge:read",
        required_scope="knowledge:read",
        side_effect=False,
        external=False,
    )
