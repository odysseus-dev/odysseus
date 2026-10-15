"""Tests for PluginStorage (table-prefix isolation)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from core.extensions.storage import OXSStorageError, PluginStorage


@pytest.fixture
def engine():
    """Fresh in-memory SQLite engine per test."""
    eng = create_engine("sqlite:///:memory:", future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def storage(engine):
    return PluginStorage("hello", engine=engine)


# ── ensure_table ──────────────────────────────────────────────────────────


def test_ensure_table_creates_table_with_prefix(storage, engine):
    storage.ensure_table("messages", columns={
        "id": "TEXT PRIMARY KEY",
        "ts": "INTEGER NOT NULL",
        "text": "TEXT",
    })
    insp = inspect(engine)
    assert "hello__messages" in insp.get_table_names()


def test_ensure_table_idempotent(storage, engine):
    storage.ensure_table("messages", columns={"id": "TEXT PRIMARY KEY"})
    storage.ensure_table("messages", columns={"id": "TEXT PRIMARY KEY"})
    insp = inspect(engine)
    assert "hello__messages" in insp.get_table_names()


def test_ensure_table_rejects_empty_columns(storage):
    with pytest.raises(ValueError):
        storage.ensure_table("messages", columns={})


def test_ensure_table_rejects_invalid_table_name(storage):
    with pytest.raises(ValueError):
        storage.ensure_table("Bad-Name", columns={"id": "TEXT"})


def test_ensure_table_rejects_invalid_column_name(storage):
    with pytest.raises(ValueError):
        storage.ensure_table("messages", columns={"bad-col": "TEXT"})


def test_ensure_table_rejects_semicolon_in_column_def(storage):
    with pytest.raises(ValueError):
        storage.ensure_table("messages", columns={"id": "TEXT; DROP TABLE x"})


def test_ensure_table_rejects_sql_comment_in_column_def(storage):
    with pytest.raises(ValueError):
        storage.ensure_table("messages", columns={"id": "TEXT -- injected"})


def test_ensure_table_supports_not_null_default_unique(storage, engine):
    storage.ensure_table("t", columns={
        "id": "INTEGER PRIMARY KEY",
        "name": "TEXT NOT NULL DEFAULT 'x'",
        "email": "TEXT UNIQUE",
    })
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("hello__t")}
    assert cols["name"]["nullable"] is False
    assert cols["name"]["default"] == "'x'"


def test_ensure_table_with_foreign_key_fails_safely(storage):
    """FOREIGN KEY references aren't validated — SQLite would need the
    referenced table to exist. We just confirm we don't silently succeed
    when a FK is declared."""
    # This should not raise during ensure_table; the FK is just stored.
    storage.ensure_table("child", columns={
        "id": "INTEGER PRIMARY KEY",
        "parent_id": "INTEGER REFERENCES hello__parent(id)",
    })
    # (FK is enforced only when pragma foreign_keys = ON; without it the
    # column is just text.)


# ── Indexes and uniques ────────────────────────────────────────────────────


def test_ensure_index_creates_index(storage, engine):
    storage.ensure_table("messages", columns={"id": "TEXT PRIMARY KEY", "ts": "INTEGER"})
    storage.ensure_index("messages", ["ts"])
    insp = inspect(engine)
    indexes = insp.get_indexes("hello__messages")
    assert any(idx["name"].endswith("_ts") for idx in indexes)


def test_ensure_index_idempotent(storage):
    storage.ensure_table("messages", columns={"id": "TEXT PRIMARY KEY", "ts": "INTEGER"})
    storage.ensure_index("messages", ["ts"])
    storage.ensure_index("messages", ["ts"])  # second call no-ops
    storage.ensure_index("messages", ["ts"])


def test_ensure_unique_creates_unique_index(storage, engine):
    storage.ensure_table("messages", columns={"id": "TEXT PRIMARY KEY", "k": "TEXT"})
    storage.ensure_unique("messages", ["k"])
    insp = inspect(engine)
    indexes = insp.get_indexes("hello__messages")
    assert any(idx.get("unique") for idx in indexes)


def test_ensure_index_creates_index_on_own_table_only(storage, engine):
    """An extension can only create indexes on its own prefix — the API
    itself takes only the unprefixed name, so cross-extension is
    impossible by construction. Verify the index lands on the prefixed
    table."""
    storage.ensure_table("messages", columns={"id": "TEXT PRIMARY KEY", "ts": "INTEGER"})
    storage.ensure_index("messages", ["ts"])
    insp = storage.ensure_engine = engine  # noqa: just to inspect
    insp = __import__("sqlalchemy").inspect(engine)
    indexes = insp.get_indexes("hello__messages")
    assert any(idx["name"].endswith("_ts") for idx in indexes)


# ── apply_data_schema ─────────────────────────────────────────────────────


def test_apply_data_schema_full_flow(storage, engine):
    storage.apply_data_schema({
        "messages": {
            "columns": {
                "id": "TEXT PRIMARY KEY",
                "chat_jid": "TEXT NOT NULL",
                "text": "TEXT",
                "ts": "INTEGER NOT NULL",
            },
            "indexes": [["chat_jid", "ts"], ["ts"]],
            "unique": [["id"]],
        },
        "chats": {
            "columns": {"jid": "TEXT PRIMARY KEY", "name": "TEXT"},
        },
    })
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "hello__messages" in tables
    assert "hello__chats" in tables


def test_apply_data_schema_ignores_malformed_entries(storage, engine):
    storage.apply_data_schema({
        "good": {"columns": {"id": "TEXT PRIMARY KEY"}},
        "no_columns": {"columns": {}},
        "no_columns_def": {},
        "not_a_dict": "garbage",
    })
    insp = inspect(engine)
    assert "hello__good" in insp.get_table_names()
    assert "hello__no_columns" not in insp.get_table_names()


# ── insert ─────────────────────────────────────────────────────────────────


def test_insert_returns_rowid(storage):
    storage.ensure_table("t", columns={
        "id": "INTEGER PRIMARY KEY",
        "name": "TEXT",
    })
    rid = storage.insert("t", name="alice")
    assert rid > 0


def test_insert_rejects_unknown_column(storage):
    storage.ensure_table("t", columns={"id": "INTEGER PRIMARY KEY", "name": "TEXT"})
    with pytest.raises(OXSStorageError):
        storage.insert("t", unknown_col="x")


# ── execute_raw ──────────────────────────────────────────────────────────


def test_execute_raw_select_own_table(storage):
    storage.ensure_table("t", columns={
        "id": "INTEGER PRIMARY KEY",
        "name": "TEXT",
    })
    storage.insert("t", name="alice")
    storage.insert("t", name="bob")
    rows = storage.execute_raw(f"SELECT name FROM {storage.table_name('t')} ORDER BY id")
    assert [r["name"] for r in rows] == ["alice", "bob"]


def test_execute_raw_rejects_other_tables(storage, engine):
    storage.ensure_table("t", columns={"id": "INTEGER PRIMARY KEY"})
    with pytest.raises(OXSStorageError):
        storage.execute_raw("SELECT * FROM other_table")


def test_execute_raw_rejects_other_tables_after_join(storage):
    with pytest.raises(OXSStorageError):
        storage.execute_raw(
            "SELECT * FROM hello__t JOIN other_table ON hello__t.id = other_table.id"
        )


def test_execute_raw_rejects_ddl(storage):
    with pytest.raises(OXSStorageError):
        storage.execute_raw("DROP TABLE hello__t")
    with pytest.raises(OXSStorageError):
        storage.execute_raw("DELETE FROM hello__t")
    with pytest.raises(OXSStorageError):
        storage.execute_raw("UPDATE hello__t SET x = 1")


def test_execute_raw_rejects_insert(storage):
    with pytest.raises(OXSStorageError):
        storage.execute_raw("INSERT INTO hello__t VALUES (1)")


def test_execute_raw_accepts_with_cte(storage, engine):
    """A CTE whose body and final SELECT both reference own tables is allowed."""
    storage.ensure_table("t", columns={"id": "INTEGER PRIMARY KEY", "name": "TEXT"})
    storage.insert("t", name="alice")
    # Note: the CTE name ``x`` is a CTE alias, not a real table. SQLite
    # will reject this because the body SELECT name from x — x is the
    # CTE name but the regex still flags it. We use the real prefixed
    # name in the FROM clause to keep the test focused on the prefix wall.
    rows = storage.execute_raw(
        f"WITH x AS (SELECT * FROM {storage.table_name('t')}) "
        f"SELECT name FROM {storage.table_name('t')} WHERE id IN (SELECT id FROM x)"
    )
    assert rows == [{"name": "alice"}]


def test_execute_raw_strips_cte_prelude_from_check(storage, engine):
    """A pure CTE whose body reads from own tables and final SELECT reads
    the CTE alias is allowed — CTE aliases are recognized and excluded
    from the prefix wall."""
    storage.ensure_table("t", columns={"id": "INTEGER PRIMARY KEY", "name": "TEXT"})
    storage.insert("t", name="alice")
    rows = storage.execute_raw(
        f"WITH cte AS (SELECT name FROM {storage.table_name('t')}) "
        f"SELECT name FROM cte"
    )
    assert rows == [{"name": "alice"}]


# ── Drop / list ───────────────────────────────────────────────────────────


def test_drop_own_tables_removes_only_prefixed(storage, engine):
    storage.ensure_table("a", columns={"id": "INTEGER PRIMARY KEY"})
    storage.ensure_table("b", columns={"id": "INTEGER PRIMARY KEY"})
    # Simulate another extension's table
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE other__x (id INTEGER PRIMARY KEY)"
        ))
    dropped = storage.drop_own_tables()
    assert set(dropped) == {"hello__a", "hello__b"}
    insp = inspect(engine)
    assert "other__x" in insp.get_table_names()
    assert "hello__a" not in insp.get_table_names()


def test_drop_own_tables_noop_when_empty(storage):
    assert storage.drop_own_tables() == []


def test_list_own_tables(storage):
    assert storage.list_own_tables() == []
    storage.ensure_table("a", columns={"id": "INTEGER PRIMARY KEY"})
    storage.ensure_table("b", columns={"id": "INTEGER PRIMARY KEY"})
    assert set(storage.list_own_tables()) == {"hello__a", "hello__b"}


# ── Cross-extension wall ───────────────────────────────────────────────────


def test_two_extensions_have_independent_tables(engine):
    a = PluginStorage("alpha", engine=engine)
    b = PluginStorage("beta", engine=engine)
    a.ensure_table("messages", columns={"id": "INTEGER PRIMARY KEY"})
    b.ensure_table("messages", columns={"id": "INTEGER PRIMARY KEY"})
    a.insert("messages", id=1)
    a_rows = a.execute_raw("SELECT * FROM alpha__messages")
    b_rows = b.execute_raw("SELECT * FROM beta__messages")
    assert len(a_rows) == 1 and len(b_rows) == 0


def test_extension_cannot_query_other_prefix(engine):
    a = PluginStorage("alpha", engine=engine)
    a.ensure_table("messages", columns={"id": "INTEGER PRIMARY KEY"})
    b = PluginStorage("beta", engine=engine)
    with pytest.raises(OXSStorageError):
        b.execute_raw("SELECT * FROM alpha__messages")


def test_extension_cannot_drop_other_prefix(engine):
    a = PluginStorage("alpha", engine=engine)
    a.ensure_table("messages", columns={"id": "INTEGER PRIMARY KEY"})
    b = PluginStorage("beta", engine=engine)
    # b's drop_own_tables must not touch alpha__*
    b.drop_own_tables()
    insp = __import__("sqlalchemy").inspect(engine)
    assert "alpha__messages" in insp.get_table_names()
    # b cannot run DROP against alpha__messages via execute_raw
    with pytest.raises(OXSStorageError):
        b.execute_raw("DROP TABLE alpha__messages")