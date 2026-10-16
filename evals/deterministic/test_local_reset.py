"""A local reset must clear learned/generated data without disconnecting accounts."""

import pytest

from pu.db import connect
from pu.ops.reset import reset_local_data


def test_reset_removes_generated_data_and_preserves_connections(tmp_path):
    home = tmp_path / ".pu"
    conn = connect(home)
    conn.execute("INSERT INTO facts(subject,content) VALUES('old','old learned fact')")
    conn.execute("INSERT INTO chat_log(role,content) VALUES('assistant','old answer')")
    conn.commit()
    conn.close()
    retained = [
        "credentials.json",
        "gmail-token.json",
        "google-token.json",
        "drive-token.json",
    ]
    for name in retained:
        (home / name).write_text("unchanged config", encoding="utf-8")
    for name in ["MEMORY.md", "SOUL.md", "usage.jsonl", "connections_health.json"]:
        (home / name).write_text("old data", encoding="utf-8")
    for name in ["reports", "traces", "outbox", "knowledge"]:
        (home / name).mkdir()
        (home / name / "old.txt").write_text("old data", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "safe.txt").write_text("must survive")
    (home / "linked-data").symlink_to(outside, target_is_directory=True)
    result = reset_local_data(home)
    assert (outside / "safe.txt").read_text() == "must survive"
    for name in retained:
        assert (home / name).read_text() == "unchanged config"
        assert name in result["preserved"]
    assert not (home / "MEMORY.md").exists()
    assert not (home / "SOUL.md").exists()
    assert not (home / "reports").exists()
    assert not list((home / "traces").iterdir())
    assert not list((home / "outbox").iterdir())
    assert (home / "reset.marker").read_text()
    conn = connect(home)
    for table in [
        "facts",
        "episodes",
        "chat_log",
        "crm_emails",
        "knowledge_sources",
        "knowledge_chunks",
        "research_workspaces",
        "workspace_sources",
        "workspace_reports",
        "workspace_messages",
        "workspace_deliveries",
        "workflow_runs",
        "workflow_stage_checkpoints",
        "proposed_actions",
        "briefing_jobs",
        "briefing_seen",
        "agent_events",
        "tool_audit_events",
        "calendar_events",
    ]:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("marker", ["pyproject.toml", ".git"])
def test_reset_refuses_source_directory(tmp_path, marker):
    connect(tmp_path).close()
    (tmp_path / marker).write_text("protect")
    with pytest.raises(ValueError):
        reset_local_data(tmp_path)
    assert (tmp_path / "state.db").exists()


def test_reset_refuses_symlink_or_unknown_database(tmp_path):
    home = tmp_path / "home"
    connect(home).close()
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    with pytest.raises(ValueError):
        reset_local_data(alias)
    other = tmp_path / "other"
    other.mkdir()
    import sqlite3

    sqlite3.connect(other / "state.db").close()
    with pytest.raises(ValueError):
        reset_local_data(other)
    assert (other / "state.db").exists()
