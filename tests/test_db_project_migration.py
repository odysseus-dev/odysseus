import os
import sqlite3
import tempfile


def test_migrate_add_sessions_project_id(tmp_path, monkeypatch):
    """The migration must add `project_id` + index when missing and be a
    no-op when already present (idempotent)."""
    db_path = tmp_path / "test.db"
    # Seed a minimal sessions table that does NOT yet have project_id.
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr("core.database.DATABASE_URL", f"sqlite:///{db_path}")

    from core.database import _migrate_add_sessions_project_id
    _migrate_add_sessions_project_id()

    conn = sqlite3.connect(str(db_path))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    indexes = [row[1] for row in conn.execute("PRAGMA index_list(sessions)").fetchall()]
    conn.close()

    assert "project_id" in cols
    assert any(idx.startswith("ix_sessions_project_id") for idx in indexes)

    # Idempotent — second call does not error.
    _migrate_add_sessions_project_id()