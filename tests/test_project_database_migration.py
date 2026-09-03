import sqlite3

import core.database as database


def test_project_migration_repairs_missing_session_project_index(tmp_path, monkeypatch):
    db_path = tmp_path / "partial-project-migration.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, project_id TEXT)")

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path}")

    database._migrate_add_project_columns()

    with sqlite3.connect(db_path) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(sessions)").fetchall()}
    assert "ix_sessions_project_id" in indexes
