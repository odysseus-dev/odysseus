"""Regression: fire-reminder endpoint crashed with NameError on _gcu.

The route called _gcu(request) but _gcu was never defined or imported.
get_current_user (already imported at module level) is the correct call.
"""
import sys
import types
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest


def _install_stubs(monkeypatch):
    """Stub heavy deps so note_routes can be imported without a real DB."""
    for mod in ("core.auth", "sqlalchemy.orm.attributes"):
        if mod not in sys.modules:
            monkeypatch.setitem(sys.modules, mod, MagicMock())

    fake_db_mod = types.ModuleType("core.database")
    fake_db_mod.SessionLocal = MagicMock()
    fake_db_mod.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db_mod)

    fake_auth = types.ModuleType("src.auth_helpers")
    fake_auth.get_current_user = lambda req: getattr(req, "_test_user", None)
    fake_auth.require_user = lambda req: None
    monkeypatch.setitem(sys.modules, "src.auth_helpers", fake_auth)

    fake_settings = types.ModuleType("src.settings")
    fake_settings.load_settings = MagicMock(return_value={})
    monkeypatch.setitem(sys.modules, "src.settings", fake_settings)

    fake_dispatch = AsyncMock(return_value={"synthesis": "ok", "email_sent": False})
    return fake_dispatch


def test_fire_reminder_uses_get_current_user_not_gcu(monkeypatch):
    """fire_reminder must not raise NameError; owner must come from get_current_user."""
    import importlib

    fake_dispatch = _install_stubs(monkeypatch)

    import routes.note_routes as note_mod
    importlib.reload(note_mod)

    monkeypatch.setattr(note_mod, "dispatch_reminder", fake_dispatch)

    router = note_mod.setup_note_routes(task_scheduler=None)

    # Find the fire-reminder endpoint
    handler = None
    for route in router.routes:
        if hasattr(route, "path") and route.path.endswith("/fire-reminder"):
            handler = route.endpoint
            break

    assert handler is not None, "fire-reminder route not found in router"

    req = MagicMock()
    req._test_user = "alice"
    req.json = AsyncMock(return_value={"note_id": "n1", "title": "Test", "body": "hello"})

    result = asyncio.run(handler(req))

    fake_dispatch.assert_awaited_once()
    kw = fake_dispatch.call_args.kwargs
    assert kw["owner"] == "alice"
    assert kw["note_id"] == "n1"


def test_fire_reminder_owner_empty_when_anonymous(monkeypatch):
    """Anonymous callers (get_current_user → None) yield owner=''."""
    import importlib

    fake_dispatch = _install_stubs(monkeypatch)

    # Override get_current_user to return None (anonymous)
    anon_auth = types.ModuleType("src.auth_helpers")
    anon_auth.get_current_user = lambda req: None
    anon_auth.require_user = lambda req: None
    monkeypatch.setitem(sys.modules, "src.auth_helpers", anon_auth)

    import routes.note_routes as note_mod
    importlib.reload(note_mod)
    monkeypatch.setattr(note_mod, "dispatch_reminder", fake_dispatch)

    router = note_mod.setup_note_routes(task_scheduler=None)

    handler = None
    for route in router.routes:
        if hasattr(route, "path") and route.path.endswith("/fire-reminder"):
            handler = route.endpoint
            break

    req = MagicMock()
    req.json = AsyncMock(return_value={"note_id": "n2", "title": "X", "body": ""})

    asyncio.run(handler(req))

    kw = fake_dispatch.call_args.kwargs
    assert kw["owner"] == ""
