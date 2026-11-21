"""`note_shares` tables created before the cascade, and the paths that delete notes.

Earlier sharing test builds left an empty `note_shares` on some DBs with a
plain `REFERENCES notes (id)` and the unique index named
`ix_note_shares_note_user`. `create_all` skips an existing table, so on those
DBs a shared note could not be deleted (FK enforcement is on for every app
connection). `_migrate_note_shares_cascade` rebuilds the table from the model;
every note-delete path also removes the note's grants itself.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as db
from core.database import CalendarCal, CalendarEvent, Note, NoteShare

# Copied verbatim from a DB that ran the earlier build (sqlite_master).
LEGACY_DDL = """
CREATE TABLE note_shares (
	id VARCHAR NOT NULL,
	note_id VARCHAR NOT NULL,
	shared_with VARCHAR NOT NULL,
	permission VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(note_id) REFERENCES notes (id)
);
CREATE INDEX ix_note_shares_shared_with ON note_shares (shared_with);
CREATE INDEX ix_note_shares_id ON note_shares (id);
CREATE INDEX ix_note_shares_note_id ON note_shares (note_id);
CREATE UNIQUE INDEX ix_note_shares_note_user ON note_shares (note_id, shared_with);
"""

NOW = "2026-06-01 10:00:00"


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{path}")
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    class Env:
        def raw(self):
            return sqlite3.connect(path)

        def make_legacy(self):
            with self.raw() as c:
                c.execute("DROP TABLE note_shares")
                c.executescript(LEGACY_DDL)

        def seed(self, note_id, shares=(), **note):
            with factory() as s:
                s.add(Note(id=note_id, owner="alice", content="x", **note))
                s.commit()
            with self.raw() as c:
                for i, user in enumerate(shares):
                    c.execute(
                        "INSERT INTO note_shares VALUES (?,?,?,?,?,?)",
                        (f"{note_id}-s{i}", note_id, user, "edit", NOW, NOW),
                    )

        def fks(self):
            with self.raw() as c:
                return c.execute("PRAGMA foreign_key_list(note_shares)").fetchall()

        def indexes(self):
            with self.raw() as c:
                return {
                    name: sql for name, sql in c.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='note_shares'"
                    )
                }

        def grants(self):
            with self.raw() as c:
                return sorted(c.execute("SELECT note_id, shared_with, permission FROM note_shares").fetchall())

    env = Env()
    env.factory = factory
    yield env
    engine.dispose()


def _orm_delete(env, note_id):
    with env.factory() as s:
        s.delete(s.get(Note, note_id))
        s.commit()


def test_legacy_table_blocks_deleting_a_shared_note(app_db):
    """The failure the migration exists for, pinned so the fixture stays honest."""
    app_db.make_legacy()
    app_db.seed("n1", shares=["bob"])
    with pytest.raises(IntegrityError):
        _orm_delete(app_db, "n1")


def test_migration_rebuilds_legacy_table_with_cascade_and_model_indexes(app_db):
    app_db.make_legacy()
    app_db.seed("n1", shares=["bob", "carol"])
    app_db.seed("n2", shares=["bob"])

    db._migrate_note_shares_cascade()

    (fk,) = app_db.fks()
    assert (fk[2], fk[3], fk[4], fk[6]) == ("notes", "note_id", "id", "CASCADE")
    indexes = app_db.indexes()
    assert "ix_note_shares_note_user" not in indexes
    assert "UNIQUE" in indexes["ux_note_shares_note_user"]
    assert {"ix_note_shares_id", "ix_note_shares_note_id", "ix_note_shares_shared_with"} <= set(indexes)
    assert app_db.grants() == [("n1", "bob", "edit"), ("n1", "carol", "edit"), ("n2", "bob", "edit")]

    _orm_delete(app_db, "n1")
    assert app_db.grants() == [("n2", "bob", "edit")]


def test_migration_matches_a_fresh_create_all(app_db, tmp_path):
    fresh = sqlite3.connect(tmp_path / "app.db")
    expected_table = fresh.execute("SELECT sql FROM sqlite_master WHERE name='note_shares'").fetchone()[0]
    fresh.close()
    expected_indexes = app_db.indexes()

    app_db.make_legacy()
    db._migrate_note_shares_cascade()

    with app_db.raw() as c:
        rebuilt = c.execute("SELECT sql FROM sqlite_master WHERE name='note_shares'").fetchone()[0]
    # ALTER TABLE ... RENAME quotes the new name; the definition is otherwise identical.
    assert rebuilt.replace('"note_shares"', "note_shares") == expected_table
    assert app_db.indexes() == expected_indexes


def test_migration_drops_orphan_rows_and_is_idempotent(app_db):
    app_db.make_legacy()
    app_db.seed("n1", shares=["bob"])
    with app_db.raw() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO note_shares VALUES ('o1','gone','bob','view',?,?)", (NOW, NOW))

    db._migrate_note_shares_cascade()
    first = (app_db.fks(), app_db.indexes(), app_db.grants())
    db._migrate_note_shares_cascade()

    assert app_db.grants() == [("n1", "bob", "edit")]
    assert (app_db.fks(), app_db.indexes(), app_db.grants()) == first


def test_failed_rebuild_rolls_back_and_keeps_the_old_table(app_db, caplog):
    """A failure after the old table is dropped (here: an index name the
    rebuild needs is already taken) undoes the whole rebuild, logs, and leaves
    the legacy table and its rows as they were; a later boot can retry."""
    app_db.make_legacy()
    app_db.seed("n1", shares=["bob", "carol"])
    with app_db.raw() as c:
        c.execute("CREATE INDEX ux_note_shares_note_user ON notes (id)")
    before = (app_db.fks(), app_db.indexes(), app_db.grants())

    with caplog.at_level("WARNING", logger="core.database"):
        db._migrate_note_shares_cascade()

    assert "note_shares cascade migration skipped" in caplog.text
    assert (app_db.fks(), app_db.indexes(), app_db.grants()) == before
    assert before[0][0][6] != "CASCADE"
    with app_db.raw() as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE name = 'note_shares_new'").fetchall() == []
        c.execute("DROP INDEX ux_note_shares_note_user")
    db._migrate_note_shares_cascade()
    assert app_db.fks()[0][6] == "CASCADE"
    assert app_db.grants() == [("n1", "bob", "edit"), ("n1", "carol", "edit")]


def test_current_table_is_left_alone(app_db):
    app_db.seed("n1", shares=["bob"])
    with app_db.raw() as c:
        before = c.execute("SELECT rowid, * FROM note_shares").fetchall()
    db._migrate_note_shares_cascade()
    with app_db.raw() as c:
        assert c.execute("SELECT rowid, * FROM note_shares").fetchall() == before


def test_missing_table_is_a_no_op(app_db):
    with app_db.raw() as c:
        c.execute("DROP TABLE note_shares")
    db._migrate_note_shares_cascade()
    assert app_db.fks() == []


def test_calendar_reminder_delete_removes_grants_on_a_legacy_table(app_db):
    """`_delete_calendar_reminders_for_event` must not rely on the cascade."""
    from routes.calendar_routes import _delete_calendar_reminders_for_event

    app_db.make_legacy()
    start = datetime(2026, 6, 2, 9, 0)
    app_db.seed(
        "r1", shares=["bob"], title="Reminder: Dentist", label="calendar",
        source="calendar", due_date=start - timedelta(minutes=10),
    )
    with app_db.factory() as s:
        s.add(CalendarCal(id="c1", owner="alice", name="Personal"))
        ev = CalendarEvent(uid="e1", calendar_id="c1", summary="Dentist", dtstart=start, dtend=start, all_day=False)
        s.add(ev)
        s.commit()
        assert _delete_calendar_reminders_for_event(s, "alice", ev) == 1
        s.commit()
        assert s.query(Note).count() == 0
        assert s.query(NoteShare).count() == 0


def _legacy_variant(app_db, ddl, rows):
    with app_db.raw() as c:
        c.execute("DROP TABLE note_shares")
        c.executescript(ddl)
        cols = [r[1] for r in c.execute("PRAGMA table_info(note_shares)")]
        for row in rows:
            c.execute(
                f"INSERT INTO note_shares ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                tuple(row.values()),
            )
    return cols


def test_rebuild_keeps_rows_without_timestamp_columns(app_db, caplog):
    app_db.seed("n1")
    _legacy_variant(app_db, """
        CREATE TABLE note_shares (
            id VARCHAR NOT NULL PRIMARY KEY, note_id VARCHAR NOT NULL,
            shared_with VARCHAR NOT NULL, permission VARCHAR,
            FOREIGN KEY(note_id) REFERENCES notes (id));
    """, [
        {"id": "s1", "note_id": "n1", "shared_with": "bob", "permission": "edit"},
        {"id": "s2", "note_id": "n1", "shared_with": "carol", "permission": None},
        {"id": "s3", "note_id": "n1", "shared_with": "dave", "permission": "admin"},
    ])
    with caplog.at_level("INFO", logger="core.database"):
        db._migrate_note_shares_cascade()
    assert app_db.fks()[0][6] == "CASCADE"
    assert app_db.grants() == [("n1", "bob", "edit"), ("n1", "carol", "view"), ("n1", "dave", "view")]
    with app_db.raw() as c:
        assert all(c.execute("SELECT created_at IS NOT NULL AND updated_at IS NOT NULL FROM note_shares").fetchall())
    assert "3 row(s) kept, 0 dropped" in caplog.text


def test_rebuild_collapses_duplicates_without_a_unique_index(app_db, caplog):
    app_db.seed("n1")
    app_db.seed("n2")
    _legacy_variant(app_db, """
        CREATE TABLE note_shares (
            id VARCHAR NOT NULL PRIMARY KEY, note_id VARCHAR NOT NULL,
            shared_with VARCHAR NOT NULL, permission VARCHAR NOT NULL,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
            FOREIGN KEY(note_id) REFERENCES notes (id));
        CREATE INDEX ix_note_shares_note_id ON note_shares (note_id);
    """, [
        {"id": "a", "note_id": "n1", "shared_with": "bob", "permission": "view", "created_at": NOW, "updated_at": NOW},
        {"id": "b", "note_id": "n1", "shared_with": " Bob ", "permission": "edit", "created_at": NOW, "updated_at": NOW},
        {"id": "c", "note_id": "n2", "shared_with": "bob", "permission": "view", "created_at": NOW, "updated_at": NOW},
        {"id": "d", "note_id": "n2", "shared_with": "", "permission": "view", "created_at": NOW, "updated_at": NOW},
    ])
    with caplog.at_level("INFO", logger="core.database"):
        db._migrate_note_shares_cascade()
    assert app_db.fks()[0][6] == "CASCADE", caplog.text
    assert app_db.grants() == [("n1", "bob", "edit"), ("n2", "bob", "view")]
    assert "2 row(s) kept, 2 dropped: 0 without a note, 1 without a username, 1 duplicate(s)" in caplog.text
    db._migrate_note_shares_cascade()  # a second boot finds a current table
    assert app_db.grants() == [("n1", "bob", "edit"), ("n2", "bob", "view")]
