"""Pin the leak-safety of the session-mode DB helpers.

chat_routes.py persists a session's "mode" in three best-effort spots (read
current mode, persist the effective mode, set research_pending). Those spots
previously hand-rolled `SessionLocal()` with `.close()` as the LAST statement
inside a try/except — so any error before close() (e.g. a SQLite "database is
locked" under concurrent streams) leaked the connection. With the default
QueuePool for file SQLite (5 + 10 overflow), accumulated leaks exhaust the
pool and the app can no longer obtain a DB session until restart.

The logic now lives in core.database.{get,set}_session_mode, which route
through get_db_session() (commit/rollback + guaranteed close). These tests pin
that a mid-operation DB error neither raises out of the helper nor leaks the
connection. The error-path cases fail against the old close()-inside-try
pattern.
"""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Generator
from unittest.mock import MagicMock


def _load_db_helpers():
    """Load only the helper bodies under test, without importing SQLAlchemy."""
    db_path = Path(__file__).parents[1] / "core" / "database.py"
    tree = ast.parse(db_path.read_text(encoding="utf-8"), filename=str(db_path))
    wanted = {"get_db_session", "get_session_mode", "set_session_mode"}
    helper_nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "contextmanager": contextmanager,
        "Generator": Generator,
        "Session": MagicMock(),
        "SessionLocal": MagicMock(),
        "logger": MagicMock(),
    }
    exec(compile(ast.Module(helper_nodes, type_ignores=[]), str(db_path), "exec"), namespace)
    return SimpleNamespace(**namespace, _namespace=namespace)


def _mock_session(monkeypatch):
    """Make get_db_session() hand out a MagicMock session (no real DB)."""
    db = _load_db_helpers()
    sess = MagicMock()
    monkeypatch.setitem(db._namespace, "SessionLocal", lambda: sess)
    return db, sess


def test_set_session_mode_commits_and_closes_on_success(monkeypatch):
    db, sess = _mock_session(monkeypatch)
    assert db.set_session_mode("s1", "agent") is True
    sess.query.return_value.filter.return_value.update.assert_called_once_with({"mode": "agent"})
    sess.commit.assert_called_once()
    sess.close.assert_called_once()


def test_set_session_mode_does_not_leak_on_error(monkeypatch):
    db, sess = _mock_session(monkeypatch)
    sess.query.return_value.filter.return_value.update.side_effect = RuntimeError("database is locked")
    # Best-effort: the error is swallowed and False returned...
    assert db.set_session_mode("s1", "agent") is False
    # ...and crucially the connection is still returned to the pool.
    sess.rollback.assert_called_once()
    sess.close.assert_called_once()


def test_get_session_mode_reads_and_closes(monkeypatch):
    db, sess = _mock_session(monkeypatch)
    sess.query.return_value.filter.return_value.scalar.return_value = "research_pending"
    assert db.get_session_mode("s1") == "research_pending"
    sess.close.assert_called_once()


def test_get_session_mode_does_not_leak_on_error(monkeypatch):
    db, sess = _mock_session(monkeypatch)
    sess.query.return_value.filter.return_value.scalar.side_effect = RuntimeError("database is locked")
    assert db.get_session_mode("s1") is None
    sess.close.assert_called_once()
