"""Overlay interactive chat defaults are 9router routes, not ModelEndpoints.

Agents: GET /api/default-chat must return automatic/fast/balanced/best with
empty endpoint fields. Leftover endpoint names must not become overlay chat.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.helpers.import_state import preserve_import_state

with preserve_import_state("core.database", "src.database", "routes.model_routes", "routes.prefs_routes"):
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes


def _get_default_chat():
    router = model_routes.setup_model_routes(model_discovery=None)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/default-chat" and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("GET /api/default-chat missing")


def _request(user="alice", admin=False):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(is_admin=lambda _u: admin)
            )
        ),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def test_default_chat_returns_saved_overlay_route(monkeypatch):
    monkeypatch.setattr(
        model_routes,
        "_load_settings",
        lambda: {"default_model": "fast", "share_defaults_with_users": False},
    )
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda _user: {})
    result = _get_default_chat()(_request(admin=True))
    assert result["model"] == "fast"
    assert result["route"] == "fast"
    assert result["endpoint_id"] == ""
    assert result["endpoint_url"] == ""


def test_default_chat_leftover_endpoint_name_becomes_automatic(monkeypatch):
    monkeypatch.setattr(
        model_routes,
        "_load_settings",
        lambda: {
            "default_model": "qwen-3.6",
            "default_endpoint_id": "ep-1",
            "share_defaults_with_users": True,
        },
    )
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda _user: {})
    result = _get_default_chat()(_request(admin=True))
    assert result["model"] == "automatic"
    assert result["endpoint_id"] == ""


def test_default_chat_empty_prefs_are_automatic(monkeypatch):
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda _user: {})
    result = _get_default_chat()(_request(admin=False))
    assert result["model"] == "automatic"
    assert result["route"] == "automatic"
