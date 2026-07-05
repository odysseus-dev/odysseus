"""Regression: _sanitize_fts_query must not emit a bare FTS5 boolean operator.

The function exists to keep user input from raising sqlite3.OperationalError in
an FTS5 MATCH. But a bareword token that is a reserved operator (uppercase AND,
OR, NOT) was passed through verbatim, so `... MATCH 'AND'` still raised. Such
tokens are now quoted and matched as literal words.
"""
import sqlite3

import pytest

from src.session_search import _sanitize_fts_query


def _fts_match_ok(match_query):
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    except sqlite3.OperationalError:
        pytest.skip("sqlite build has no fts5")
    con.execute("INSERT INTO t(body) VALUES ('alpha and beta or gamma not delta')")
    # Must not raise OperationalError.
    con.execute("SELECT 1 FROM t WHERE t MATCH ?", (match_query,)).fetchall()


def test_reserved_operators_are_quoted():
    for op in ("AND", "OR", "NOT"):
        out = _sanitize_fts_query(op)
        assert out == f'"{op}"'
        _fts_match_ok(out)  # raised before the fix


def test_query_containing_operator_does_not_raise():
    out = _sanitize_fts_query("cats AND dogs")
    assert out is not None
    _fts_match_ok(out)


def test_plain_words_unchanged():
    assert _sanitize_fts_query("hello world") == "hello world"
    # lowercase and/or/not are not FTS5 operators and stay as plain words
    assert _sanitize_fts_query("salt and pepper") == "salt and pepper"
