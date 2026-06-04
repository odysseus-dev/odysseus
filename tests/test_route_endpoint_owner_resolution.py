"""Regression guards for owner-scoped route helper endpoint resolution.

These routes already know the request/session owner before dispatching LLM
helper calls. Their endpoint resolution must preserve that owner so a
multi-user install cannot select another user's private ModelEndpoint/API key.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_calls(path: str, function_name: str, call_name: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return [
                child for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == call_name
            ]
    raise AssertionError(f"{function_name} not found in {path}")


def _assert_calls_are_owner_scoped(path: str, function_name: str, call_name: str) -> None:
    calls = _function_calls(path, function_name, call_name)
    assert calls, f"{function_name} has no {call_name} calls"
    missing = [
        call.lineno for call in calls
        if not any(keyword.arg == "owner" for keyword in call.keywords)
    ]
    assert not missing, f"{path}:{function_name} has unscoped {call_name} calls at {missing}"


def test_document_ai_tidy_resolves_endpoints_with_owner():
    _assert_calls_are_owner_scoped(
        "routes/document_routes.py",
        "ai_tidy_documents",
        "resolve_task_endpoint",
    )
    _assert_calls_are_owner_scoped(
        "routes/document_routes.py",
        "ai_tidy_documents",
        "resolve_endpoint",
    )


def test_note_reminder_synthesis_resolves_endpoints_with_owner():
    _assert_calls_are_owner_scoped(
        "routes/note_routes.py",
        "dispatch_reminder",
        "resolve_endpoint",
    )


def test_calendar_quick_parse_resolves_endpoints_with_owner():
    _assert_calls_are_owner_scoped(
        "routes/calendar_routes.py",
        "quick_parse",
        "resolve_endpoint",
    )


def test_task_parse_resolves_endpoints_with_owner():
    _assert_calls_are_owner_scoped(
        "routes/task_routes.py",
        "parse_task",
        "resolve_endpoint",
    )


def test_history_compact_resolves_endpoints_with_owner():
    _assert_calls_are_owner_scoped(
        "routes/history_routes.py",
        "compact_session",
        "resolve_endpoint",
    )
