"""Server contracts behind Settings scope presentation.

`/api/auth/status` reports whether the caller may change server-wide settings
(`can_manage_settings`) and whether accounts exist at all (`auth_enabled`).
These mirror `require_admin()`: an explicit AUTH_ENABLED=false operator may
manage settings although `is_admin` stays false. They are presentation hints;
the write routes keep authorizing on their own.

`PATCH /api/prefs` writes several of the caller's preferences in one store
write so paired values (endpoint and model) cannot be half applied.
"""

import json

import pytest

import routes.prefs_routes as prefs_routes


class _AuthManager:
    is_configured = True
    signup_enabled = False

    def get_username_for_token(self, token):
        return {"admin-token": "admin", "user-token": "user"}.get(token)

    def is_admin(self, user):
        return user == "admin"

    def status(self, token):
        username = self.get_username_for_token(token)
        return {
            "configured": True,
            "authenticated": username is not None,
            "username": username,
            "is_admin": self.is_admin(username) if username else False,
        }

    def get_privileges(self, username):
        return {}


@pytest.fixture
def auth_client(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from routes import auth_routes

    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    app = fastapi.FastAPI()
    app.include_router(auth_routes.setup_auth_routes(_AuthManager()))
    return TestClient(app)


@pytest.mark.parametrize(
    ("auth_env", "token", "expected"),
    [
        ("true", "admin-token", {"auth_enabled": True, "can_manage_settings": True, "is_admin": True}),
        ("true", "user-token", {"auth_enabled": True, "can_manage_settings": False, "is_admin": False}),
        ("true", None, {"auth_enabled": True, "can_manage_settings": False, "is_admin": False}),
        ("false", None, {"auth_enabled": False, "can_manage_settings": True, "is_admin": False}),
    ],
)
def test_status_reports_settings_access_like_require_admin(auth_client, monkeypatch, auth_env, token, expected):
    monkeypatch.setenv("AUTH_ENABLED", auth_env)
    if token:
        auth_client.cookies.set("odysseus_session", token)
    body = auth_client.get("/api/auth/status").json()
    assert {key: body[key] for key in expected} == expected


def test_status_access_hint_does_not_grant_settings_writes(auth_client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    auth_client.cookies.set("odysseus_session", "user-token")
    assert auth_client.get("/api/auth/status").json()["can_manage_settings"] is False
    assert auth_client.post("/api/auth/settings", json={"default_model": "x"}).status_code == 403


@pytest.fixture
def prefs_client(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({"_users": {
        "alice": {"theme": "light", "default_model": "old-model"},
        "bob": {"default_model": "bob-model"},
    }}), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    current = {"user": "alice"}
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda request: current["user"])
    app = fastapi.FastAPI()
    app.include_router(prefs_routes.setup_prefs_routes())
    return TestClient(app), prefs_file, current


def test_patch_prefs_writes_pairs_and_removals_for_the_caller_only(prefs_client):
    client, prefs_file, _ = prefs_client
    response = client.patch("/api/prefs", json={"values": {
        "default_endpoint_id": "ep-beta",
        "default_model": "beta-chat",
        "theme": None,
    }})
    assert response.status_code == 200
    assert response.json() == {"values": {
        "default_endpoint_id": "ep-beta",
        "default_model": "beta-chat",
        "theme": None,
    }}
    stored = json.loads(prefs_file.read_text())["_users"]
    assert stored["alice"] == {"default_model": "beta-chat", "default_endpoint_id": "ep-beta"}
    assert stored["bob"] == {"default_model": "bob-model"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"values": {}},
        {"values": ["default_model"]},
        {"values": {"": "x"}},
        {"values": {f"k{i}": i for i in range(prefs_routes._MAX_PATCH_KEYS + 1)}},
    ],
)
def test_patch_prefs_rejects_malformed_bodies_without_writing(prefs_client, body):
    client, prefs_file, _ = prefs_client
    before = prefs_file.read_text()
    assert client.patch("/api/prefs", json=body).status_code == 400
    assert prefs_file.read_text() == before


def test_patch_prefs_with_auth_disabled_keeps_other_users(prefs_client):
    client, prefs_file, current = prefs_client
    current["user"] = None
    assert client.patch("/api/prefs", json={"values": {"default_model": "solo"}}).status_code == 200
    stored = json.loads(prefs_file.read_text())["_users"]
    assert stored["alice"]["default_model"] == "solo"
    assert stored["bob"] == {"default_model": "bob-model"}
