"""Regression tests for chat-route API token scoping.

The real ``routes.chat_routes`` import pulls in optional auth dependencies in a
plain local checkout, so these tests parse the module source and execute only
the tiny scope helper. The route-order assertions keep bearer tokens minted for
other integrations from reaching chat/LLM work before the ``chat`` scope check.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"
CHAT_SCOPE_GUARD = "_require_chat_scope_for_api_token"
BASH_GUARD = "_reject_bash_for_api_token"


def _source_tree() -> ast.Module:
    return ast.parse(CHAT_ROUTES.read_text(encoding="utf-8"))


def _load_scope_guard():
    tree = _source_tree()
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == CHAT_SCOPE_GUARD
    )
    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "HTTPException": HTTPException,
        "Request": object,
    }
    exec(compile(module, str(CHAT_ROUTES), "exec"), namespace)
    return namespace[CHAT_SCOPE_GUARD]


def _load_bash_guard():
    tree = _source_tree()
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == BASH_GUARD
    )
    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "HTTPException": HTTPException,
        "Request": object,
    }
    exec(compile(module, str(CHAT_ROUTES), "exec"), namespace)
    return namespace[BASH_GUARD]


def _request(*, api_token=False, scopes=None):
    state = {"api_token": api_token}
    if scopes is not None:
        state["api_token_scopes"] = scopes
    return SimpleNamespace(state=SimpleNamespace(**state))


def test_chat_scope_guard_allows_cookie_sessions():
    guard = _load_scope_guard()

    guard(_request(api_token=False))


def test_chat_scope_guard_allows_chat_scoped_bearer_tokens():
    guard = _load_scope_guard()

    guard(_request(api_token=True, scopes=["documents:read", "chat"]))


def test_bash_guard_allows_non_bash_requests():
    guard = _load_bash_guard()

    guard(_request(api_token=True, scopes=["chat"]), "false")


def test_bash_guard_allows_cookie_sessions():
    guard = _load_bash_guard()

    guard(_request(api_token=False), "true")


@pytest.mark.parametrize("scopes", [None, [], ["chat"], ["documents:read", "chat"]])
def test_bash_guard_rejects_bearer_tokens(scopes):
    guard = _load_bash_guard()

    with pytest.raises(HTTPException) as exc:
        guard(_request(api_token=True, scopes=scopes), "true")

    assert exc.value.status_code == 403
    assert "bash" in str(exc.value.detail)


@pytest.mark.parametrize("scopes", [None, [], ["documents:read"], ["memory:read"]])
def test_chat_scope_guard_rejects_non_chat_bearer_tokens(scopes):
    guard = _load_scope_guard()

    with pytest.raises(HTTPException) as exc:
        guard(_request(api_token=True, scopes=scopes))

    assert exc.value.status_code == 403
    assert "chat" in str(exc.value.detail)


def _calls_scope_guard(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.Expr):
        return False
    call = statement.value
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == CHAT_SCOPE_GUARD
    )


def _first_executable_statement(handler: ast.AsyncFunctionDef) -> ast.stmt:
    statements = list(handler.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    assert statements, f"{handler.name} has no executable body"
    return statements[0]


def test_chat_routes_check_api_token_scope_before_work():
    tree = _source_tree()
    guarded_handlers = {
        "chat_endpoint",
        "chat_stream",
        "chat_resume",
        "chat_stop",
        "chat_stream_status",
        "inject_context",
        "search_messages",
        "rewrite_message",
    }
    handlers = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in guarded_handlers
    }

    assert set(handlers) == guarded_handlers
    for name, handler in handlers.items():
        first_statement = _first_executable_statement(handler)
        assert _calls_scope_guard(first_statement), (
            f"{name} must call {CHAT_SCOPE_GUARD} before parsing, owner checks, "
            "session access, or LLM work"
        )
