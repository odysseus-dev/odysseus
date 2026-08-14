"""Admin-gated app-config routes must honor AUTH_ENABLED=false.

Regression test for the inconsistency between core.middleware.require_admin
(which allows requests when the operator disabled auth) and the inline admin
checks in routes/auth_routes.py (which returned 403 unconditionally, so
settings changes were silently lost behind reverse-proxy auth setups).
"""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from routes import auth_routes

    stored = {}
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(stored))
    monkeypatch.setattr(auth_routes, "_save_settings", stored.update)
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)

    class _AuthManager:
        is_configured = True

        def get_username_for_token(self, token):
            return "admin" if token == "session-token" else None

        def is_admin(self, user):
            return user == "admin"

    app = fastapi.FastAPI()
    app.include_router(auth_routes.setup_auth_routes(_AuthManager()))
    return TestClient(app), stored


def test_set_settings_allowed_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c, stored = client
    r = c.post("/api/auth/settings", json={"default_model": "some-model"})
    assert r.status_code == 200
    assert stored.get("default_model") == "some-model"


def test_get_settings_unscrubbed_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c, stored = client
    stored["brave_api_key"] = "sk-secret"
    r = c.get("/api/auth/settings")
    assert r.status_code == 200
    assert r.json()["brave_api_key"] == "sk-secret"


def test_set_settings_still_403_when_auth_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, _ = client
    r = c.post("/api/auth/settings", json={"default_model": "x"})
    assert r.status_code == 403


def test_set_settings_admin_session_still_works(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, stored = client
    c.cookies.set("odysseus_session", "session-token")
    r = c.post("/api/auth/settings", json={"default_model": "admin-model"})
    assert r.status_code == 200
    assert stored.get("default_model") == "admin-model"
