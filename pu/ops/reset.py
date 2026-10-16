"""Reset local application data while retaining account and model configuration.

The caller must stop the dashboard/worker first, so open SQLite connections
cannot retain or rewrite the old state. This never contacts Google or a model.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from pu.db import connect

CONFIG_FILES = {
    "credentials.json",
    "gmail-token.json",
    "gmail-send-token.json",
    "calendar-token.json",
    "google-token.json",
    "google-calendar-token.json",
    "drive-token.json",
    "models.json",
    ".env",
}


def reset_local_data(home: Path) -> dict:
    home = Path(home)
    if home.is_symlink() or not home.is_dir() or not (home / "state.db").is_file():
        raise ValueError("Chỉ reset thư mục dữ liệu PU đã tồn tại và không phải symlink.")
    if home.resolve() == Path(home.anchor) or any(
        (home / name).exists() for name in (".git", "pyproject.toml")
    ):
        raise ValueError("Từ chối reset thư mục gốc hoặc thư mục source code.")
    # Verify the database belongs to this application before deleting files.
    import sqlite3

    verification = sqlite3.connect(home / "state.db")
    try:
        tables = {
            row[0] for row in verification.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        verification.close()
    if not {"crm_emails", "chat_log", "facts"} <= tables:
        raise ValueError("Không nhận diện được database PU; chưa xoá dữ liệu.")
    removed = []
    for child in home.iterdir():
        if child.name in CONFIG_FILES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed.append(child.name)
    conn = connect(home)
    # Give the user a clean start before the next normal background sync.
    conn.execute("INSERT INTO briefing_state VALUES(?,?)", ("last_scan_epoch", str(time.time())))
    conn.commit()
    conn.close()
    (home / "reset.marker").write_text(uuid.uuid4().hex, encoding="utf-8")
    for name in ("traces", "outbox"):
        (home / name).mkdir(exist_ok=True)
    return {
        "removed": sorted(removed),
        "preserved": sorted(name for name in CONFIG_FILES if (home / name).exists()),
    }
