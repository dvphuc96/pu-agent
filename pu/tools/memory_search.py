"""Read-only long-term memory search for the specialized Memory Agent.

Read-only memory retrieval is intentionally separate from write administration so an agent can inspect facts
and episodes without carrying any write capability or entering Human Approval.
"""

from __future__ import annotations

from pu.tools.registry import Tool


def make_tool(memory) -> Tool:
    def search_memory(query: str, top_k: int = 5) -> str:
        query = (query or "").strip()
        if not query:
            return "A non-empty memory query is required."
        limit = max(1, min(int(top_k or 5), 20))
        facts = memory.facts.search(query, top_k=limit)
        episodes = memory.episodes.search(query, top_k=limit)
        lines: list[str] = []
        if facts:
            lines.append("Semantic facts:")
            lines.extend(f"- {item}" for item in facts)
        if episodes:
            if lines:
                lines.append("")
            lines.append("Relevant episodes:")
            lines.extend(f"- {item}" for item in episodes)
        if not lines:
            return "No matching long-term memory found."
        return "\n".join(lines)

    return Tool(
        name="search_memory",
        description=(
            "Search PU's read-only long-term semantic facts and episodic history. "
            "Use this for historical context. It cannot update or delete memory."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "company, contact, topic, or event to recall"},
                "top_k": {"type": "integer", "description": "maximum matches per memory type (default 5)"},
            },
            "required": ["query"],
        },
        fn=search_memory,
        capability="memory:read",
        required_scope="memory:read",
        side_effect=False,
        external=False,
    )
