"""Model-assisted route helpers must resolve endpoints with owner scope."""

import ast
from pathlib import Path


def _function_source(path: str, name: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found in {path}")


def test_document_ai_tidy_resolves_with_owner_scope():
    body = _function_source("routes/document_routes.py", "ai_tidy_documents")
    assert "resolve_task_endpoint(owner=user or None)" in body
    assert 'resolve_endpoint("default", owner=user or None)' in body


def test_calendar_quick_parse_resolves_with_owner_scope():
    body = _function_source("routes/calendar_routes.py", "quick_parse")
    assert "owner = _require_user(request)" in body
    assert 'resolve_endpoint("utility", owner=owner or None)' in body
    assert 'resolve_endpoint("default", owner=owner or None)' in body


def test_task_parse_resolves_with_owner_scope():
    body = _function_source("routes/task_routes.py", "parse_task")
    assert "user = _owner(request)" in body
    assert 'resolve_endpoint("utility", owner=user or None)' in body
    assert 'resolve_endpoint("default", owner=user or None)' in body


def test_history_compact_resolves_with_owner_scope():
    body = _function_source("routes/history_routes.py", "compact_session")
    assert "owner = effective_user(request)" in body
    assert 'resolve_endpoint("utility", owner=owner or None)' in body


def test_note_reminder_synthesis_resolves_with_owner_scope():
    body = _function_source("routes/note_routes.py", "dispatch_reminder")
    assert 'resolve_endpoint("utility", owner=owner or None)' in body
    assert 'resolve_endpoint("default", owner=owner or None)' in body
