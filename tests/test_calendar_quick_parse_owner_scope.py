"""Owner-scope regression for POST /api/calendar/quick-parse.

quick_parse authenticates the caller with _require_user(request) but used to
discard the return value, then resolve the natural-language parsing LLM with
resolve_endpoint("utility") / resolve_endpoint("default") and NO owner. With no
owner, resolve_endpoint skips owner_filter, so in a multi-user deployment the
parse call could be dispatched against another user's private endpoint config
(including their api_key).

Sibling handlers in this route file capture the owner and thread it into both
data queries and endpoint lookups; this handler was missed. This test pins that
quick_parse passes owner="alice" to resolve_endpoint. It fails on the original
code (owner dropped) and passes once owner is threaded through.
"""
import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _install_calendar_db_stub(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    for name in [
        "Base",
        "Document",
        "DocumentVersion",
        "Session",
        "ChatMessage",
        "GalleryImage",
        "GalleryAlbum",
        "Note",
        "ScheduledTask",
        "TaskRun",
        "ModelEndpoint",
        "Webhook",
        "CalendarCal",
        "CalendarEvent",
    ]:
        setattr(db, name, MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)
    return db


def _install_multipart_stub(monkeypatch):
    multipart = types.ModuleType("python_multipart")
    multipart.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", multipart)


def _install_quick_parse_dep_stubs(monkeypatch, resolve_mock):
    """Stub the modules quick_parse imports lazily at call time.

    The handler does `from src.endpoint_resolver import resolve_endpoint`,
    `from src.llm_core import llm_call_async` and
    `from src.text_helpers import strip_think` inside the function body, so we
    intercept by swapping those modules in sys.modules before invocation.
    """
    resolver = types.ModuleType("src.endpoint_resolver")
    resolver.resolve_endpoint = resolve_mock
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", resolver)

    async def _fake_llm_call_async(*_args, **_kwargs):
        return (
            '{"summary": "lunch", "dtstart": "2026-06-05T13:00:00", '
            '"dtend": "2026-06-05T14:00:00", "all_day": false, '
            '"location": "downtown", "description": "", "confidence": 0.9}'
        )

    llm_core = types.ModuleType("src.llm_core")
    llm_core.llm_call_async = _fake_llm_call_async
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)

    text_helpers = types.ModuleType("src.text_helpers")
    text_helpers.strip_think = lambda raw, **_kwargs: raw
    monkeypatch.setitem(sys.modules, "src.text_helpers", text_helpers)


def _import_calendar_routes(monkeypatch):
    _install_calendar_db_stub(monkeypatch)
    _install_multipart_stub(monkeypatch)
    monkeypatch.delitem(sys.modules, "routes.calendar_routes", raising=False)
    return __import__(
        "routes.calendar_routes", fromlist=["setup_calendar_routes"]
    )


def _route_endpoint(calendar_routes, path, method):
    router = calendar_routes.setup_calendar_routes()
    full_path = f"/api/calendar{path}"
    for route in router.routes:
        if route.path == full_path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full_path}")


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def test_quick_parse_passes_owner_to_resolve_endpoint(monkeypatch):
    calendar_routes = _import_calendar_routes(monkeypatch)
    monkeypatch.setattr(calendar_routes, "_require_user", lambda request: "alice")

    resolve_mock = MagicMock(
        return_value=("http://endpoint/v1/chat", "model-x", {"Authorization": "Bearer k"})
    )
    _install_quick_parse_dep_stubs(monkeypatch, resolve_mock)

    quick_parse = _route_endpoint(calendar_routes, "/quick-parse", "POST")

    out = asyncio.run(quick_parse(_Request({"text": "lunch friday 1pm downtown"})))

    assert out.get("ok") is True
    # "utility" is configured (truthy url returned first), so only that call
    # fires; it must carry the authenticated owner so resolve_endpoint can
    # owner_filter the endpoint row instead of resolving another user's config.
    resolve_mock.assert_called_once_with("utility", owner="alice")
