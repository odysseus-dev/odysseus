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
    body = _function_source("routes/document/document_routes.py", "ai_tidy_documents")
    assert "resolve_task_endpoint(owner=user or None)" in body
    assert 'resolve_endpoint("default", owner=user or None)' in body


def test_calendar_quick_parse_resolves_with_owner_scope():
    body = _function_source("routes/calendar_routes.py", "quick_parse")
    assert "owner = _require_user(request)" in body
    assert 'resolve_endpoint("utility", owner=owner or None)' in body
    assert 'resolve_endpoint("default", owner=owner or None)' in body


def test_task_parse_resolves_with_owner_scope():
    body = _function_source("routes/task/task_routes.py", "parse_task")
    assert "user = _owner(request)" in body
    assert 'resolve_endpoint("utility", owner=user or None)' in body
    assert 'resolve_endpoint("default", owner=user or None)' in body


def test_history_compact_resolves_with_owner_scope():
    body = _function_source("routes/history/history_routes.py", "compact_session")
    assert "owner = effective_user(request)" in body
    tree = ast.parse(body)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_endpoint"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "utility"
    ]
    assert len(calls) == 1

    kwargs = {
        kw.arg: ast.unparse(kw.value)
        for kw in calls[0].keywords
    }
    assert kwargs["owner"] == "owner or None"
    assert kwargs["fallback_url"] == "getattr(session, 'endpoint_url', None)"
    assert kwargs["fallback_model"] == "getattr(session, 'model', None)"
    assert kwargs["fallback_headers"] == "getattr(session, 'headers', None)"


def test_note_reminder_synthesis_resolves_with_owner_scope():
    body = _function_source("routes/note/note_routes.py", "dispatch_reminder")
    assert 'resolve_endpoint("utility", owner=owner or None)' in body
    assert 'resolve_endpoint("default", owner=owner or None)' in body
