"""Regression: parse_task must scope its LLM endpoint lookup by caller owner.

The POST /api/tasks/parse handler resolves an endpoint to run the
natural-language task parser. In a multi-user deploy it must pass the caller's
owner to resolve_endpoint so it cannot pick up another user's private endpoint
configuration and API key. Every other handler in the file starts with
`user = _owner(request)` and scopes its lookups; parse_task must do the same.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

import routes.task_routes as task_routes
import src.endpoint_resolver as endpoint_resolver
import src.llm_core as llm_core


def _get_parse_task_handler():
    """Pull the parse_task closure out of the router built by setup_task_routes."""
    router = task_routes.setup_task_routes(task_scheduler=object())
    for route in router.routes:
        if getattr(route, "path", None) == "/api/tasks/parse":
            return route.endpoint
    raise AssertionError("parse_task route not registered")


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def test_parse_task_passes_owner_to_resolve_endpoint(monkeypatch):
    captured = {}

    def fake_resolve_endpoint(setting_prefix, *args, **kwargs):
        captured.setdefault("calls", []).append((setting_prefix, kwargs.get("owner")))
        # Return a usable endpoint so the handler proceeds to the LLM call.
        return ("http://endpoint", "model-x", {})

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(
        llm_core, "llm_call_async", AsyncMock(return_value='{"prompt": "do it"}')
    )
    # _owner(request) calls get_current_user; pretend the caller is alice.
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")

    handler = _get_parse_task_handler()
    result = asyncio.run(handler(_FakeRequest({"description": "summarize my email daily at 7am"})))

    assert result.get("success") is True, result
    assert captured.get("calls"), "resolve_endpoint was never called"
    # The first (and any) resolve_endpoint call must carry the caller's owner.
    prefix, owner = captured["calls"][0]
    assert prefix == "utility"
    assert owner == "alice", f"resolve_endpoint called without owner scope: {captured['calls']}"


def test_parse_task_fallback_default_also_scopes_owner(monkeypatch):
    """When 'utility' yields no URL, the 'default' fallback must also be owner-scoped."""
    captured = {}

    def fake_resolve_endpoint(setting_prefix, *args, **kwargs):
        captured.setdefault("calls", []).append((setting_prefix, kwargs.get("owner")))
        if setting_prefix == "utility":
            return (None, None, None)
        return ("http://endpoint", "model-x", {})

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(
        llm_core, "llm_call_async", AsyncMock(return_value='{"prompt": "do it"}')
    )
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")

    handler = _get_parse_task_handler()
    result = asyncio.run(handler(_FakeRequest({"description": "summarize my email daily at 7am"})))

    assert result.get("success") is True, result
    calls = dict(captured.get("calls", []))
    assert calls.get("utility") == "alice", captured["calls"]
    assert calls.get("default") == "alice", f"default fallback dropped owner scope: {captured['calls']}"
