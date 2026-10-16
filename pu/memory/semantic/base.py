"""Semantic-memory contract used by the application layer.

PU currently ships one implementation: SQLite + FTS5. The protocol keeps
agents and tools independent from that storage choice without carrying APIs for
removed hosted-memory experiments.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


FactId = int


@runtime_checkable
class FactStore(Protocol):
    """Durable facts about the user, people, projects and preferences."""

    def add(self, subject: str, content: str, source: str = "user") -> None: ...

    def search(self, query: str, top_k: int = 4) -> list[str]: ...

    def list(self, limit: int = 200) -> list[dict]: ...

    def search_with_ids(self, query: str, top_k: int = 8) -> list[dict]: ...

    def update(self, fact_id: FactId, content: str, subject: str | None = None) -> bool: ...

    def delete(self, fact_id: FactId) -> bool: ...
