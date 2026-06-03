"""Regression: the DB fallback in get_session_history must hide the same
messages the in-memory path hides.

The in-memory branch skips messages whose metadata has ``hidden`` (e.g.
compaction summaries that are kept for AI context but not shown to the user).
The DB fallback (taken when the in-memory history is empty, e.g. after a
restart) built the client response from every DB row with no such filter, so
hidden messages leaked to the client on DB-served sessions. The rebuilt
in-memory ``session.history`` must still keep them, though, so only the response
is filtered.

get_session_history depends on the DB, the session manager and a FastAPI
request, so this pins the regression at the source level (as other route tests
in this repo do).
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "routes" / "history_routes.py"


def _function_source(src_text, name):
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src_text, node)
    raise AssertionError(f"{name} not found in {SRC}")


def test_db_fallback_filters_hidden_from_response():
    src = _function_source(SRC.read_text(), "get_session_history")
    marker = "load from DB"
    assert marker in src, "expected the DB fallback block in get_session_history"
    db_section = src.split(marker, 1)[1]
    assert "hidden" in db_section, (
        "the DB-fallback path must filter `hidden` messages from the response "
        "to match the in-memory path"
    )
