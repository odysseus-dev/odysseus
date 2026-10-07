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
