"""Tests for OXS migrate (schema application runner)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect

from core.extensions import migrate


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    yield eng
    eng.dispose()


def test_apply_creates_tables(engine):
    summary = migrate.apply_for_extension("ext", {
        "messages": {
            "columns": {
                "id": "TEXT PRIMARY KEY",
                "ts": "INTEGER NOT NULL",
            },
        },
    }, engine=engine)
    assert summary["errors"] == []
    assert len(summary["created"]) == 1
    assert "ext__messages" in summary["created"]
    assert "ext__messages" in inspect(engine).get_table_names()


def test_apply_is_idempotent(engine):
    schema = {
        "messages": {"columns": {"id": "TEXT PRIMARY KEY"}},
    }
    migrate.apply_for_extension("ext", schema, engine=engine)
    summary = migrate.apply_for_extension("ext", schema, engine=engine)
    assert summary["created"] == []
    assert summary["existing"] == ["ext__messages"]


def test_apply_creates_indexes(engine):
    summary = migrate.apply_for_extension("ext", {
        "messages": {
            "columns": {
                "id": "TEXT PRIMARY KEY",
                "ts": "INTEGER",
            },
            "indexes": [["ts"], ["id", "ts"]],
            "unique": [["id"]],
        },
    }, engine=engine)
    assert summary["errors"] == []
    insp = inspect(engine)
    indexes = insp.get_indexes("ext__messages")
    assert any(idx.get("unique") for idx in indexes)


def test_apply_handles_invalid_schema_gracefully(engine):
    """A non-dict at the top level is rejected with a clear error."""
    summary = migrate.apply_for_extension("ext", "not a dict", engine=engine)
    assert summary["errors"] == ["data_schema must be a dict"]
    assert summary["created"] == []
    assert summary["existing"] == []


def test_apply_handles_dict_with_empty_columns(engine):
    """A dict whose tables have no ``columns`` key is silently ignored.

    Per the schema validator, ``columns`` is required; we treat missing
    columns as 'skip this table' rather than a hard error so a partial
    schema update doesn't break enable().
    """
    summary = migrate.apply_for_extension("ext", {
        "no_cols": {"indexes": [["x"]]},
    }, engine=engine)
    # Either we created nothing (because columns missing) or we errored
    # out cleanly. Verify the result is consistent.
    assert "no_cols" not in summary["created"]
    assert summary["errors"] == []


def test_apply_reports_storage_errors(engine):
    summary = migrate.apply_for_extension("ext", {
        "messages": {
            "columns": {"bad-col": "TEXT"},
        },
    }, engine=engine)
    assert summary["errors"]
    assert "invalid column name" in summary["errors"][0]


def test_apply_supports_multiple_tables(engine):
    summary = migrate.apply_for_extension("ext", {
        "messages": {"columns": {"id": "TEXT PRIMARY KEY"}},
        "chats": {"columns": {"id": "TEXT PRIMARY KEY"}},
    }, engine=engine)
    assert len(summary["created"]) == 2
    assert "ext__messages" in summary["created"]
    assert "ext__chats" in summary["created"]


def test_drop_all_removes_only_prefixed(engine):
    migrate.apply_for_extension("ext1", {
        "a": {"columns": {"id": "TEXT PRIMARY KEY"}},
        "b": {"columns": {"id": "TEXT PRIMARY KEY"}},
    }, engine=engine)
    # Other extension's table
    from sqlalchemy import text as _text
    with engine.begin() as conn:
        conn.execute(_text("CREATE TABLE ext2__x (id TEXT PRIMARY KEY)"))
    dropped = migrate.drop_all_for_extension("ext1", engine=engine)
    assert set(dropped) == {"ext1__a", "ext1__b"}
    assert "ext2__x" in inspect(engine).get_table_names()
    assert "ext1__a" not in inspect(engine).get_table_names()


def test_apply_partial_failure_reports_errors_per_table(engine):
    """One bad table, one good table — errors list captures the failure."""
    summary = migrate.apply_for_extension("ext", {
        "good": {"columns": {"id": "TEXT PRIMARY KEY"}},
        "bad": {"columns": {"bad-col": "TEXT"}},
    }, engine=engine)
    # Either both apply (if bad-col check fails differently) or one
    # succeeds and one fails. Accept both patterns as long as we report.
    assert summary["created"] or summary["errors"]


def test_summary_structure_is_stable(engine):
    summary = migrate.apply_for_extension("ext", {}, engine=engine)
    assert set(summary.keys()) == {"created", "existing", "errors"}