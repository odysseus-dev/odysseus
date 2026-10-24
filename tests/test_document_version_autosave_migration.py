"""Existing version histories must remain intact and protected on upgrade."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


def test_legacy_versions_remain_protected_after_idempotent_migration(tmp_path, monkeypatch):
    import core.database as database

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE document_versions (
                    id VARCHAR PRIMARY KEY, document_id VARCHAR NOT NULL,
                    version_number INTEGER NOT NULL, content TEXT NOT NULL,
                    summary VARCHAR, source VARCHAR, created_at DATETIME
                )
            """))
            conn.execute(text("""
                INSERT INTO document_versions
                VALUES ('v1', 'doc', 1, 'Saved text', 'Saved version', 'user', '2026-10-07 12:00:00')
            """))

        database._migrate_add_document_version_autosave_column()
        database._migrate_add_document_version_autosave_column()
        columns = {column["name"] for column in inspect(engine).get_columns("document_versions")}
        assert "is_autosave" in columns
        with sessionmaker(bind=engine)() as db:
            saved = db.query(database.DocumentVersion).one()
            assert saved.content == "Saved text"
            assert saved.summary == "Saved version"
            assert saved.source == "user"
            assert saved.version_number == 1
            assert saved.created_at.isoformat() == "2026-10-07T12:00:00"
            assert saved.is_autosave is False

        # Older SQL writers also default to a protected checkpoint.
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO document_versions (id, document_id, version_number, content)
                VALUES ('v2', 'doc', 2, 'Another snapshot')
            """))
        with sessionmaker(bind=engine)() as db:
            assert db.get(database.DocumentVersion, "v2").is_autosave is False
    finally:
        engine.dispose()


def test_migration_failure_raises_and_recovers(tmp_path, monkeypatch):
    import pytest
    from unittest.mock import patch
    import core.database as database

    engine = create_engine(f"sqlite:///{(tmp_path / 'failure_recovery.db').as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE document_versions (
                    id VARCHAR PRIMARY KEY, document_id VARCHAR NOT NULL,
                    version_number INTEGER NOT NULL, content TEXT NOT NULL,
                    summary VARCHAR, source VARCHAR, created_at DATETIME
                )
            """))

        # Simulate a transient failure during ALTER TABLE
        orig_begin = engine.begin

        class FailingBegin:
            def __init__(self, begin_fn):
                self._begin = begin_fn

            def __enter__(self):
                raise RuntimeError("Disk I/O error or database is locked")

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch.object(engine, "begin", return_value=FailingBegin(orig_begin)):
            with pytest.raises(RuntimeError, match="Disk I/O error or database is locked"):
                database._migrate_add_document_version_autosave_column()

        # Schema still lacks the column
        columns_before = {col["name"] for col in inspect(engine).get_columns("document_versions")}
        assert "is_autosave" not in columns_before

        # Recovery: once the issue clears, re-running the migration succeeds
        database._migrate_add_document_version_autosave_column()
        columns_after = {col["name"] for col in inspect(engine).get_columns("document_versions")}
        assert "is_autosave" in columns_after
    finally:
        engine.dispose()
