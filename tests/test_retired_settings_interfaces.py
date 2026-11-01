"""Retired settings stay stored but cannot leak through generic interfaces."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import core.database as database
import routes.auth_routes as auth_routes
import src.settings as settings_mod
from src.agent_tools.admin_tools import do_manage_settings


LEGACY_VALUE = [
    {"endpoint_id": "private-endpoint-id", "model": "private-model-name"},
]


class _AuthManager:
    def get_username_for_token(self, token):
        return "admin" if token == "admin-session" else None

    def is_admin(self, username):
        return username == "admin"


class _Request(SimpleNamespace):
    def __init__(self, body=None, *, admin=False):
        super().__init__(
            cookies={
                auth_routes.SESSION_COOKIE: "admin-session"
            } if admin else {},
            _body=body,
        )

    async def json(self):
        return self._body


def _route(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


@pytest.mark.asyncio
async def test_generic_settings_hide_and_preserve_retired_fallbacks(monkeypatch):
    store = {
        **settings_mod.DEFAULT_SETTINGS,
        "default_model_fallbacks": list(LEGACY_VALUE),
        "tts_enabled": True,
    }

    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(store))

    def save_settings(updated):
        store.clear()
        store.update(updated)

    monkeypatch.setattr(auth_routes, "_save_settings", save_settings)
    router = auth_routes.setup_auth_routes(_AuthManager())
    get_settings = _route(router, "/api/auth/settings", "GET")
    set_settings = _route(router, "/api/auth/settings", "POST")

    anonymous = await get_settings(_Request())
    admin = await get_settings(_Request(admin=True))

    assert "default_model_fallbacks" not in anonymous
    assert "default_model_fallbacks" not in admin
    assert store["default_model_fallbacks"] == LEGACY_VALUE

    response = await set_settings(_Request({
        "default_model_fallbacks": [],
        "tts_enabled": False,
    }, admin=True))

    assert "default_model_fallbacks" not in response
    assert store["default_model_fallbacks"] == LEGACY_VALUE
    assert store["tts_enabled"] is False


def test_manage_settings_tombstones_legacy_fallback_key(monkeypatch):
    store = {
        **settings_mod.DEFAULT_SETTINGS,
        "default_model_fallbacks": list(LEGACY_VALUE),
    }
    save_calls = []

    class _Db:
        def close(self):
            return None

    monkeypatch.setattr(database, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(store))

    def save_settings(updated):
        save_calls.append(dict(updated))
        store.clear()
        store.update(updated)

    monkeypatch.setattr(settings_mod, "save_settings", save_settings)

    listed = asyncio.run(do_manage_settings(json.dumps({"action": "list"})))
    assert "default_model_fallbacks" not in listed["settings"]

    for action in ("get", "set", "reset", "delete"):
        payload = {"action": action, "key": "default_model_fallbacks"}
        if action == "set":
            payload["value"] = []
        result = asyncio.run(do_manage_settings(json.dumps(payload)))
        assert result["exit_code"] == 1
        assert "Unknown setting" in result["error"]

    assert save_calls == []
    assert store["default_model_fallbacks"] == LEGACY_VALUE
