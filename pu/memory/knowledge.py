"""Knowledge — documents the user teaches PU, searchable at answer time.

The fourth store beside semantic (facts), episodic and procedural (skills).
A knowledge SOURCE is one document — pasted text, an uploaded file, a file in
the watched `.pu/knowledge/` folder, or a Google Drive import. Its text is
chunked at paragraph boundaries (~800 chars) into knowledge_chunks, mirrored
into knowledge_fts exactly the way facts_fts mirrors facts.

Design points this file is held to:
  Pure SQLite, no network — the module never imports a Google library.
  Disabled sources stay stored but invisible to search (a query-time filter,
  so re-enabling is instant and nothing is lost).
  Re-index is an upsert keyed by path + content fingerprint: unchanged files
  are skipped, changed files are replaced, files that vanished are removed.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

CHUNK_LIMIT = 800  # characters per chunk — a few paragraphs, FTS-friendly


# ---- chunking --------------------------------------------------------------

def chunk_text(text: str) -> list[str]:
    """Split at blank lines, then pack whole paragraphs up to CHUNK_LIMIT.
    A single paragraph longer than the limit still becomes its own chunk —
    truncating would silently lose knowledge."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return []
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 > CHUNK_LIMIT:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def _fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ---- write paths -----------------------------------------------------------

def add_source(conn: sqlite3.Connection, title: str, text: str, group: str = "",
               origin: str = "paste", path: str = "", enabled: bool = True) -> int:
    """Insert one source and its chunks. Returns the source id."""
    cur = conn.execute(
        """INSERT INTO knowledge_sources (title, "group", origin, path, fingerprint, enabled, chars)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, group or "Ungrouped", origin, path,
         _fingerprint(text), 1 if enabled else 0, len(text)),
    )
    source_id = cur.lastrowid
    for chunk in chunk_text(text):
        conn.execute("INSERT INTO knowledge_chunks (source_id, content) VALUES (?, ?)",
                     (source_id, chunk))
    conn.commit()
    return source_id


def _replace_text(conn, source_id: int, text: str) -> None:
    conn.execute("DELETE FROM knowledge_chunks WHERE source_id=?", (source_id,))
    for chunk in chunk_text(text):
        conn.execute("INSERT INTO knowledge_chunks (source_id, content) VALUES (?, ?)",
                     (source_id, chunk))
    conn.execute("UPDATE knowledge_sources SET chars=?, fingerprint=? WHERE id=?",
                 (len(text), _fingerprint(text), source_id))
    conn.commit()


def set_enabled(conn: sqlite3.Connection, source_id: int, enabled: bool) -> None:
    conn.execute("UPDATE knowledge_sources SET enabled=? WHERE id=?",
                 (1 if enabled else 0, source_id))
    conn.commit()


def set_group(conn: sqlite3.Connection, source_id: int, group: str) -> None:
    conn.execute('UPDATE knowledge_sources SET "group"=? WHERE id=?',
                 (group.strip() or "Ungrouped", source_id))
    conn.commit()


def delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    # Chunks first — the FTS 'delete' trigger needs the rows still present.
    conn.execute("DELETE FROM knowledge_chunks WHERE source_id=?", (source_id,))
    conn.execute("DELETE FROM knowledge_sources WHERE id=?", (source_id,))
    conn.commit()


# ---- read paths ------------------------------------------------------------

def search(conn: sqlite3.Connection, query: str, top_k: int = 5) -> list[dict]:
    """FTS5 match over ENABLED sources. The query is user/model text, so wrap
    it in double quotes — an FTS operator in the input would otherwise be a
    syntax error instead of just a word."""
    safe = '"' + (query or "").replace('"', '""') + '"'
    rows = conn.execute(
        """SELECT kc.content, ks.id AS source_id, ks.title, ks."group"
           FROM knowledge_fts kf
           JOIN knowledge_chunks kc ON kc.id = kf.rowid
           JOIN knowledge_sources ks ON ks.id = kc.source_id
           WHERE knowledge_fts MATCH ? AND ks.enabled = 1
           ORDER BY rank LIMIT ?""",
        (safe, top_k),
    ).fetchall()
    return [dict(r) for r in rows]


def list_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, title, "group", origin, path, enabled, chars, added_at
           FROM knowledge_sources ORDER BY "group", title"""
    ).fetchall()


def get_source(conn: sqlite3.Connection, source_id: int) -> dict | None:
    row = conn.execute(
        """SELECT id, title, "group", origin, path, enabled, chars, added_at
           FROM knowledge_sources WHERE id=?""", (source_id,)).fetchone()
    return dict(row) if row else None


def chunks_for(conn: sqlite3.Connection, source_id: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT content FROM knowledge_chunks WHERE source_id=? ORDER BY id", (source_id,))]


# ---- folder re-index ---------------------------------------------------------

def reindex_folder(conn: sqlite3.Connection, folder: Path) -> str:
    """Make the store agree with the watched folder: add new .md/.txt files,
    replace changed ones, drop sources whose file is gone. Returns a sentence."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {p.name: p for p in sorted(folder.iterdir())
             if p.is_file() and p.suffix.lower() in (".md", ".txt")}
    existing = {r["path"]: r for r in conn.execute(
        """SELECT id, path, fingerprint FROM knowledge_sources
           WHERE origin='folder'""").fetchall()}

    added = updated = 0
    for name, path in files.items():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fp = _fingerprint(text)
        old = existing.get(name)
        if old is None:
            add_source(conn, path.stem, text, origin="folder", path=name)
            added += 1
        elif old["fingerprint"] != fp:
            _replace_text(conn, old["id"], text)
            updated += 1
    removed = 0
    for name, row in existing.items():
        if name not in files:
            delete_source(conn, row["id"])
            removed += 1
    return f"Knowledge folder sync: {added} added, {updated} updated, {removed} removed."
