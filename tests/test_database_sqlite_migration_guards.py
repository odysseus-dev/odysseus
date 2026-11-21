"""Regression coverage for the SQLite-only migration guards in core.database.

Two contracts are pinned here:

* On a non-SQLite engine, every guarded migration helper returns before any
  SQLite-specific database operation (no ``sqlite3.connect`` and no
  ``engine.connect``/``engine.begin`` that would run ``PRAGMA``).
* On SQLite, representative helpers still add their columns to an older
  schema and stay idempotent when run again. Both helper styles are covered:
  direct ``sqlite3.connect`` helpers and helpers using the SQLAlchemy engine.
"""

import inspect
import logging
import re
import sqlite3
import types

import pytest
from sqlalchemy import create_engine

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as database  # noqa: E402


_SQLITE_ONLY_OPS = re.compile(r"PRAGMA|sqlite3\.connect|sqlite_master")


def _sqlite_specific_zero_arg_migrations():
    """Every zero-arg migration helper whose source uses SQLite-only operations.

    Discovery keys on the SQLite-only operations rather than on the presence
    of the guard, so a newly added helper that forgets the guard is still
    collected and fails the non-SQLite test below.
    """
    helpers = []
    for name, fn in inspect.getmembers(database, inspect.isfunction):
        if not name.startswith("_migrate_") or fn.__module__ != database.__name__:
            continue
        if not _SQLITE_ONLY_OPS.search(inspect.getsource(fn)):
            continue
        params = inspect.signature(fn).parameters.values()
        if any(p.default is inspect.Parameter.empty for p in params):
            continue
        helpers.append(name)
    return sorted(helpers)


GUARDED_MIGRATIONS = _sqlite_specific_zero_arg_migrations()


_FAKE_PG_URL = "postgresql://user:pass@db.invalid:5432/odysseus"


class _ForbiddenEngine:
    """Stand-in for a PostgreSQL engine that records any database access.

    Accesses are recorded rather than only raised because the migration
    helpers swallow exceptions into warning logs; the test asserts on the
    recorded calls so an unguarded helper cannot hide behind that handler.
    """

    dialect = types.SimpleNamespace(name="postgresql")

    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []

    def connect(self, *args, **kwargs):
        self.calls.append("engine.connect")
        raise RuntimeError("non-SQLite engine must not be touched by SQLite-only migrations")

    def begin(self, *args, **kwargs):
        self.calls.append("engine.begin")
        raise RuntimeError("non-SQLite engine must not be touched by SQLite-only migrations")


@pytest.fixture
def non_sqlite_backend(monkeypatch):
    """Patch core.database onto a fake PostgreSQL backend; yields the call log."""
    calls = []

    def _recording_sqlite_connect(*args, **kwargs):
        calls.append("sqlite3.connect")
        raise RuntimeError("sqlite3.connect must not run on a non-SQLite backend")

    real_exists = database.os.path.exists

    def _exists(path):
        # Make the "file path" derived from the Postgres URL look present so an
        # unguarded sqlite3-style helper would reach sqlite3.connect instead of
        # returning early on a missing file.
        if str(path).startswith("postgresql:"):
            return True
        return real_exists(path)

    monkeypatch.setattr(database, "engine", _ForbiddenEngine(calls))
    monkeypatch.setattr(database, "DATABASE_URL", _FAKE_PG_URL)
    monkeypatch.setattr(sqlite3, "connect", _recording_sqlite_connect)
    monkeypatch.setattr(database.os.path, "exists", _exists)
    yield calls


LEGACY_ROW_IDS = {
    "sessions": "s1",
    "model_endpoints": "e1",
    "mcp_servers": "m1",
    "scheduled_tasks": "t1",
}


@pytest.fixture
def legacy_sqlite_db(tmp_path, monkeypatch):
    """A file-backed SQLite database holding an older, column-poor schema."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE sessions (id VARCHAR PRIMARY KEY, name VARCHAR);
        INSERT INTO sessions (id, name) VALUES ('s1', 'kept');
        CREATE TABLE model_endpoints (id VARCHAR PRIMARY KEY, name VARCHAR, base_url VARCHAR);
        INSERT INTO model_endpoints (id, name, base_url) VALUES ('e1', 'kept', 'http://x/v1');
        CREATE TABLE mcp_servers (id VARCHAR PRIMARY KEY, name VARCHAR);
        INSERT INTO mcp_servers (id, name) VALUES ('m1', 'kept');
        CREATE TABLE scheduled_tasks (id VARCHAR PRIMARY KEY, name VARCHAR);
        INSERT INTO scheduled_tasks (id, name) VALUES ('t1', 'kept');
        """
    )
    conn.commit()
    conn.close()

    url = f"sqlite:///{db_file}"
    engine = create_engine(url)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "DATABASE_URL", url)
    try:
        yield db_file
    finally:
        engine.dispose()


def _columns(db_file, table):
    conn = sqlite3.connect(db_file)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _row_name(db_file, table, row_id):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(f"SELECT name FROM {table} WHERE id = ?", (row_id,)).fetchone()[0]
    finally:
        conn.close()


def test_is_sqlite_follows_engine_dialect(monkeypatch):
    monkeypatch.setattr(database, "engine", _ForbiddenEngine())
    assert database._is_sqlite() is False
    monkeypatch.setattr(database, "engine", create_engine("sqlite:///:memory:"))
    assert database._is_sqlite() is True


def test_guarded_migration_set_covers_both_helper_styles():
    # Guard against the parametrized test below silently shrinking.
    assert "_migrate_add_session_cwd_column" in GUARDED_MIGRATIONS  # sqlite3.connect style
    assert "_migrate_add_oauth_config" in GUARDED_MIGRATIONS  # SQLAlchemy engine style
    assert len(GUARDED_MIGRATIONS) >= 40


@pytest.mark.parametrize("helper_name", GUARDED_MIGRATIONS)
def test_guarded_migration_skips_sqlite_operations_on_non_sqlite(non_sqlite_backend, helper_name):
    getattr(database, helper_name)()
    assert non_sqlite_backend == []


def test_generic_owner_helper_skips_sqlite_operations_on_non_sqlite(non_sqlite_backend):
    database._migrate_add_owner_to_table("sessions", "ix_sessions_owner")
    assert non_sqlite_backend == []


SQLITE3_STYLE = [
    ("_migrate_add_session_cwd_column", "sessions", "cwd"),
    ("_migrate_add_total_cost_usd", "sessions", "total_cost_usd"),
    ("_migrate_add_model_tool_modes_column", "model_endpoints", "model_tool_modes"),
]

SQLALCHEMY_STYLE = [
    ("_migrate_add_oauth_config", "mcp_servers", "oauth_config"),
    ("_migrate_add_disabled_tools", "mcp_servers", "disabled_tools"),
    ("_migrate_add_notifications_enabled", "scheduled_tasks", "notifications_enabled"),
]


@pytest.mark.parametrize("helper_name,table,column", SQLITE3_STYLE + SQLALCHEMY_STYLE)
def test_sqlite_migration_applies_to_older_schema_and_is_idempotent(
    legacy_sqlite_db, caplog, helper_name, table, column
):
    helper = getattr(database, helper_name)
    assert column not in _columns(legacy_sqlite_db, table)

    with caplog.at_level(logging.WARNING, logger=database.__name__):
        helper()
        after_first = _columns(legacy_sqlite_db, table)
        helper()
        after_second = _columns(legacy_sqlite_db, table)

    assert after_first.count(column) == 1
    assert after_second == after_first
    assert _row_name(legacy_sqlite_db, table, LEGACY_ROW_IDS[table]) == "kept"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [
        r.getMessage() for r in caplog.records
    ]
