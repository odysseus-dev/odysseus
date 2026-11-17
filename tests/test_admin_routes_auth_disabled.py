"""Admin-gated app-config routes must honor AUTH_ENABLED=false.

Regression test for the inconsistency between core.middleware.require_admin
(which allows requests when the operator disabled auth) and the inline admin
checks in routes/auth_routes.py (which returned 403 unconditionally, so
settings changes were silently lost behind reverse-proxy auth setups).
"""
import pytest
from core.auth import SetAdminResult


class _StoreDict(dict):
    """Dict-like settings store that also holds features and integrations."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.features = {"web_search": True, "web_fetch": True, "memory": True}
        self.integrations = {
            "int-1": {
                "id": "int-1",
                "name": "miniflux-sync",
                "preset": "miniflux",
                "api_key": "sk-secret-key-123",
                "base_url": "http://127.0.0.1:8080",
            }
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from routes import auth_routes

    stored = _StoreDict()
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(stored))
    monkeypatch.setattr(auth_routes, "_save_settings", stored.update)
    monkeypatch.setattr(auth_routes, "_load_features", lambda: dict(stored.features))
    monkeypatch.setattr(auth_routes, "_save_features", stored.features.update)
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)

    monkeypatch.setattr(auth_routes, "load_integrations", lambda: list(stored.integrations.values()))
    monkeypatch.setattr(auth_routes, "get_integration", lambda iid: stored.integrations.get(iid))

    def _add_int(body):
        item = dict(body, id=f"int-{len(stored.integrations) + 1}")
        stored.integrations[item["id"]] = item
        return item

    def _update_int(iid, body):
        if iid not in stored.integrations:
            return None
        stored.integrations[iid].update(body)
        return stored.integrations[iid]

    def _delete_int(iid):
        return stored.integrations.pop(iid, None) is not None

    async def _test_call(iid, method, path):
        return {"exit_code": 0, "message": "Connection successful"}

    monkeypatch.setattr(auth_routes, "add_integration", _add_int)
    monkeypatch.setattr(auth_routes, "update_integration", _update_int)
    monkeypatch.setattr(auth_routes, "delete_integration", _delete_int)
    monkeypatch.setattr(auth_routes, "execute_api_call", _test_call)

    class _AuthManager:
        is_configured = True
        signup_enabled = False

        def get_username_for_token(self, token):
            if token in ("session-token", "admin-token"):
                return "admin"
            if token == "user-token":
                return "user"
            return None

        def is_admin(self, user):
            return user == "admin"

        def list_users(self):
            return [{"username": "admin", "is_admin": True}, {"username": "user", "is_admin": False}]

        def create_user(self, username, password, is_admin=False):
            return True

        def set_privileges(self, username, privs):
            return True

        def set_admin(self, username, is_admin, requesting_user):
            return SetAdminResult.SUCCESS

        def delete_user(self, username, requesting_user):
            return True

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


def test_settings_numeric_validation_and_clamping_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c, stored = client
    # Clamp out-of-range integer values
    r = c.post("/api/auth/settings", json={"agent_max_rounds": 999, "agent_max_tool_calls": 5000})
    assert r.status_code == 200
    assert stored["agent_max_rounds"] == 200
    assert stored["agent_max_tool_calls"] == 1000

    # Non-integer must raise 400
    r = c.post("/api/auth/settings", json={"agent_max_rounds": "not-an-int"})
    assert r.status_code == 400

    # Unknown settings keys are not saved
    r = c.post("/api/auth/settings", json={"unsupported_key_xyz": "value"})
    assert r.status_code == 200
    assert "unsupported_key_xyz" not in stored


def test_features_accessible_and_persisted_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c, stored = client
    # Public GET
    r = c.get("/api/auth/features")
    assert r.status_code == 200
    assert r.json().get("web_search") is True

    # POST allowed without login
    r = c.post("/api/auth/features", json={"web_search": False})
    assert r.status_code == 200
    assert stored.features["web_search"] is False


def test_integrations_lifecycle_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c, stored = client

    # List: keys masked
    r = c.get("/api/auth/integrations")
    assert r.status_code == 200
    items = r.json().get("integrations", [])
    assert len(items) == 1
    assert items[0]["api_key"] != "sk-secret-key-123"

    # Create
    r = c.post("/api/auth/integrations", json={"name": "new-srv", "preset": "custom", "api_key": "raw-key"})
    assert r.status_code == 200
    new_id = r.json()["integration"]["id"]
    assert new_id in stored.integrations

    # Update
    r = c.put(f"/api/auth/integrations/{new_id}", json={"name": "updated-srv"})
    assert r.status_code == 200
    assert stored.integrations[new_id]["name"] == "updated-srv"

    # Test
    r = c.post(f"/api/auth/integrations/{new_id}/test")
    assert r.status_code == 200

    # Delete
    r = c.delete(f"/api/auth/integrations/{new_id}")
    assert r.status_code == 200
    assert new_id not in stored.integrations

    # 404 for missing item
    assert c.put("/api/auth/integrations/nonexistent", json={"name": "x"}).status_code == 404
    assert c.delete("/api/auth/integrations/nonexistent").status_code == 404
    assert c.post("/api/auth/integrations/nonexistent/test").status_code == 404


def test_user_management_fails_closed_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c, _ = client

    assert c.get("/api/auth/users").status_code == 403
    assert c.post("/api/auth/users", json={"username": "hacker", "password": "password123", "is_admin": True}).status_code == 403
    assert c.put("/api/auth/users/target/privileges", json={"can_chat": False}).status_code == 403
    assert c.put("/api/auth/users/target/rename", json={"username": "newtarget"}).status_code == 403
    assert c.put("/api/auth/users/target/admin", json={"is_admin": True}).status_code == 403
    assert c.put("/api/auth/users/target/password", json={"new_password": "password123"}).status_code == 403
    assert c.put("/api/auth/open-signup", json={"enabled": True}).status_code == 403
    assert c.request("DELETE", "/api/auth/users", json={"username": "target"}).status_code == 403


def test_get_settings_scrubbed_for_unauthenticated_and_non_admin_when_auth_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, stored = client
    stored["brave_api_key"] = "sk-secret-brave"
    stored["default_model"] = "gpt-4o"

    # Unauthenticated caller gets scrubbed settings
    r = c.get("/api/auth/settings")
    assert r.status_code == 200
    data = r.json()
    assert data["brave_api_key"] == ""
    assert data["default_model"] == "gpt-4o"

    # Non-admin authenticated user gets scrubbed settings
    c.cookies.set("odysseus_session", "user-token")
    r = c.get("/api/auth/settings")
    assert r.status_code == 200
    data = r.json()
    assert data["brave_api_key"] == ""
    assert data["default_model"] == "gpt-4o"

    # Admin caller gets full unscrubbed settings
    c.cookies.set("odysseus_session", "session-token")
    r = c.get("/api/auth/settings")
    assert r.status_code == 200
    data = r.json()
    assert data["brave_api_key"] == "sk-secret-brave"
    assert data["default_model"] == "gpt-4o"


def test_set_settings_403_for_authenticated_non_admin(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, stored = client
    c.cookies.set("odysseus_session", "user-token")
    r = c.post("/api/auth/settings", json={"default_model": "user-choice"})
    assert r.status_code == 403
    assert stored.get("default_model") != "user-choice"


def test_features_gated_when_auth_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, stored = client

    # Unauthenticated
    assert c.post("/api/auth/features", json={"web_search": False}).status_code == 403

    # Non-admin
    c.cookies.set("odysseus_session", "user-token")
    assert c.post("/api/auth/features", json={"web_search": False}).status_code == 403
    assert stored.features["web_search"] is True

    # Admin
    c.cookies.set("odysseus_session", "session-token")
    r = c.post("/api/auth/features", json={"web_search": False})
    assert r.status_code == 200
    assert stored.features["web_search"] is False


def test_integrations_gated_when_auth_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, stored = client

    # Unauthenticated
    assert c.get("/api/auth/integrations").status_code == 403
    assert c.post("/api/auth/integrations", json={"name": "a"}).status_code == 403

    # Non-admin
    c.cookies.set("odysseus_session", "user-token")
    assert c.get("/api/auth/integrations").status_code == 403
    assert c.post("/api/auth/integrations", json={"name": "a"}).status_code == 403
    assert c.put("/api/auth/integrations/int-1", json={"name": "b"}).status_code == 403
    assert c.delete("/api/auth/integrations/int-1").status_code == 403
    assert c.post("/api/auth/integrations/int-1/test").status_code == 403

    # Admin
    c.cookies.set("odysseus_session", "session-token")
    assert c.get("/api/auth/integrations").status_code == 200
    assert c.post("/api/auth/integrations", json={"name": "new", "preset": "custom"}).status_code == 200


def test_user_management_requires_admin_when_auth_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    c, _ = client

    # Unauthenticated
    assert c.get("/api/auth/users").status_code == 403
    assert c.post("/api/auth/users", json={"username": "a", "password": "b"}).status_code == 403
    assert c.put("/api/auth/users/target/password", json={"new_password": "password123"}).status_code == 403

    # Non-admin
    c.cookies.set("odysseus_session", "user-token")
    assert c.get("/api/auth/users").status_code == 403
    assert c.put("/api/auth/users/target/password", json={"new_password": "password123"}).status_code == 403

    # Admin
    c.cookies.set("odysseus_session", "session-token")
    assert c.get("/api/auth/users").status_code == 200


def test_document_style_extract_gating(client, monkeypatch):
    c, _ = client
    dummy_file = {"file": ("sample.txt", b"dummy prose content")}

    # When auth enabled: unauthenticated is 403, non-admin is 403
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert c.post("/api/auth/settings/document-style/extract", files=dummy_file).status_code == 403

    c.cookies.set("odysseus_session", "user-token")
    assert c.post("/api/auth/settings/document-style/extract", files=dummy_file).status_code == 403

    # When auth disabled: unauthenticated passes admin gate (400 for sample length < 80)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    c.cookies.clear()
    assert c.post("/api/auth/settings/document-style/extract", files=dummy_file).status_code == 400
