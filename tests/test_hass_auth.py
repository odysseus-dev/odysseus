"""Home Assistant credential delegation (Supervisor auth API) tests.

Covers the src/hass_auth.py client contract and the login-route integration:
local-first verification, Supervisor delegation when enabled, provisioning
rules (HA admins → Odysseus admins), fail-closed behaviour and the login-page
status flag.
"""

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.hass_auth as hass_auth
from core.auth import AuthManager
from routes.auth_routes import setup_auth_routes


def _make_transport(responder):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "path": request.url.path,
                "body": json.loads(request.content or b"{}"),
                "auth": request.headers.get("authorization", ""),
            }
        )
        return responder(request)

    return httpx.MockTransport(handler), calls


@pytest.fixture
def auth_manager(tmp_path):
    return AuthManager(auth_path=str(tmp_path / "auth.json"))


@pytest.fixture
def client_factory(auth_manager):
    def _build():
        app = FastAPI()
        app.include_router(setup_auth_routes(auth_manager))
        return TestClient(app)

    return _build


def _enable_hass(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_HASS_AUTH", "1")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-supervisor-token")


def _patch_supervisor(monkeypatch, responder):
    transport, calls = _make_transport(responder)
    real_client = httpx.AsyncClient

    def _factory(timeout=None, **kwargs):
        return real_client(timeout=timeout, transport=transport)

    monkeypatch.setattr(hass_auth.httpx, "AsyncClient", _factory)
    return calls


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------


def test_hass_auth_disabled_without_env(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_HASS_AUTH", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert hass_auth.hass_auth_enabled() is False


def test_hass_auth_disabled_without_supervisor_token(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_HASS_AUTH", "1")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    assert hass_auth.hass_auth_enabled() is False


def test_hass_auth_enabled(monkeypatch):
    _enable_hass(monkeypatch)
    assert hass_auth.hass_auth_enabled() is True


# ---------------------------------------------------------------------------
# Supervisor client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_check_login_ok(monkeypatch):
    _enable_hass(monkeypatch)
    calls = _patch_supervisor(
        monkeypatch, lambda request: httpx.Response(200, json={"group_ids": ["system-admin"]})
    )
    ok, groups = await hass_auth.supervisor_check_login("hauser", "hapass")
    assert ok and groups == ["system-admin"]
    assert calls[0]["path"] == "/auth"
    assert calls[0]["body"]["username"] == "hauser"
    assert calls[0]["auth"] == "Bearer test-supervisor-token"
    assert hass_auth.hass_user_is_admin(groups) is True


@pytest.mark.asyncio
async def test_supervisor_check_login_rejects_bad_credentials(monkeypatch):
    _enable_hass(monkeypatch)
    _patch_supervisor(monkeypatch, lambda request: httpx.Response(401))
    ok, groups = await hass_auth.supervisor_check_login("hauser", "wrong")
    assert ok is False
    assert hass_auth.hass_user_is_admin(groups) is False


@pytest.mark.asyncio
async def test_supervisor_check_login_fails_closed_on_server_error(monkeypatch):
    _enable_hass(monkeypatch)
    _patch_supervisor(monkeypatch, lambda request: httpx.Response(500))
    ok, _ = await hass_auth.supervisor_check_login("hauser", "hapass")
    assert ok is False


# ---------------------------------------------------------------------------
# Login route integration
# ---------------------------------------------------------------------------


def test_status_reports_hass_auth_flag(client_factory, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_HASS_AUTH", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with client_factory() as c:
        assert c.get("/api/auth/status").json()["hass_auth"] is False
    _enable_hass(monkeypatch)
    with client_factory() as c:
        assert c.get("/api/auth/status").json()["hass_auth"] is True


def test_login_provisions_ha_admin_as_odysseus_admin(client_factory, monkeypatch, auth_manager):
    _enable_hass(monkeypatch)
    _patch_supervisor(
        monkeypatch, lambda request: httpx.Response(200, json={"group_ids": ["system-admin"]})
    )
    with client_factory() as c:
        resp = c.post("/api/auth/login", json={"username": "HAUser", "password": "hapass"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "username": "hauser"}
        assert "odysseus_session" in resp.cookies or resp.headers.get("set-cookie")
    # Account auto-created as an Odysseus admin.
    assert "hauser" in auth_manager.users
    assert auth_manager.is_admin("hauser")
    # Local password was NOT set to the HA password — HA stays the way in.
    assert auth_manager.verify_password("hauser", "hapass") is False


def test_login_provisions_ha_non_admin_as_regular_user(client_factory, monkeypatch, auth_manager):
    _enable_hass(monkeypatch)
    _patch_supervisor(
        monkeypatch, lambda request: httpx.Response(200, json={"group_ids": ["system-users"]})
    )
    # An admin already exists, so the HA group list decides the role.
    auth_manager.setup("localadmin", "local-password-123")
    with client_factory() as c:
        resp = c.post("/api/auth/login", json={"username": "guest", "password": "hapass"})
        assert resp.status_code == 200
    assert "guest" in auth_manager.users
    assert not auth_manager.is_admin("guest")


def test_login_refused_when_supervisor_rejects(client_factory, monkeypatch, auth_manager):
    _enable_hass(monkeypatch)
    _patch_supervisor(monkeypatch, lambda request: httpx.Response(401))
    with client_factory() as c:
        resp = c.post("/api/auth/login", json={"username": "intruder", "password": "nope"})
        assert resp.status_code == 401
    assert "intruder" not in auth_manager.users


def test_login_without_hass_auth_never_calls_supervisor(client_factory, monkeypatch, auth_manager):
    monkeypatch.delenv("ODYSSEUS_HASS_AUTH", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    calls = _patch_supervisor(
        monkeypatch, lambda request: httpx.Response(200, json={"group_ids": ["system-admin"]})
    )
    with client_factory() as c:
        resp = c.post("/api/auth/login", json={"username": "who", "password": "nope"})
        assert resp.status_code == 401
    assert calls == []
    assert "who" not in auth_manager.users


def test_login_refuses_reserved_ha_username(client_factory, monkeypatch, auth_manager):
    _enable_hass(monkeypatch)
    _patch_supervisor(
        monkeypatch, lambda request: httpx.Response(200, json={"group_ids": ["system-admin"]})
    )
    with client_factory() as c:
        # "system" is a reserved Odysseus username; Supervisor may vouch for
        # the pair, but Odysseus must not host the account.
        resp = c.post("/api/auth/login", json={"username": "system", "password": "hapass"})
        assert resp.status_code == 403
    assert "system" not in auth_manager.users


def test_local_login_still_works_with_hass_auth_enabled(client_factory, monkeypatch, auth_manager):
    _enable_hass(monkeypatch)
    calls = _patch_supervisor(
        monkeypatch, lambda request: httpx.Response(200, json={"group_ids": ["system-admin"]})
    )
    auth_manager.setup("localadmin", "local-password-123")
    with client_factory() as c:
        resp = c.post(
            "/api/auth/login", json={"username": "localadmin", "password": "local-password-123"}
        )
        assert resp.status_code == 200
    # Local verification succeeded; Supervisor never consulted.
    assert calls == []