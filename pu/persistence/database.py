"""Database boundary for durable operational state.

SQLite is the local/dev/test operational adapter. Connection lifecycle stays
behind this object so another database adapter can be introduced without
changing repositories. Operational repositories use short-lived connections
for restart recovery and concurrent readers.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SQLiteDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_home(cls, home: Path) -> "SQLiteDatabase":
        return cls(Path(home) / "state.db")

    def connect(self, *, check_same_thread: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        # WAL lets dashboard/AgentOps readers coexist with checkpoint/audit writes.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            # Read-only / unusual SQLite builds can reject this; durability still
            # works without WAL, just with less concurrency.
            pass
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect(check_same_thread=False)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
