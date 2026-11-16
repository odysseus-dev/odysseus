"""Home Assistant Supervisor ingress SSO (X-Remote-User-*) tests.

The Supervisor's ingress proxy injects the signed-in HA user's identity
headers on proxied panel requests and filters any client-supplied versions.
On the add-on's directly exposed port, though, a LAN client can send those
headers freely — so trusting them requires verifying the connection came
from the Supervisor container. These tests pin that trust boundary.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as app_module  # module-level: the app logger outlives pytest fixtures
import src.hass_auth as hass_auth
from core.auth import AuthManager
from core.middleware import SecurityHeadersMiddleware, is_ingress_request
from routes.auth_routes import SESSION_COOKIE


IDENTITY_HEADERS = {
    "X-Remote-User-Id": "ha-1234",
    "X-Remote-User-Name": "hauser",
    "X-Remote-User-Groups": "system-users",
}
ADMIN_HEADERS = {
    "X-Remote-User-Id": "ha-1",
    "X-Remote-User-Name": "haadmin",
    "X-Remote-User-Groups": "system-admin",
}


@pytest.fixture(autouse=True)
def _reset_supervisor_ip_cache():
    yield
    hass_auth._SUPERVISOR_IP_CACHE = None


def _enable_hass(monkeypatch, trusted_ip=None):
    monkeypatch.setenv("ODYSSEUS_HASS_AUTH", "1")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-supervisor-token")
    if trusted_ip is not None:
        monkeypatch.setenv("ODYSSEUS_INGRESS_TRUSTED_IP", trusted_ip)


def _request(headers, client_host="192.0.2.10"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 4321),
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# is_ingress_request — CSP/frame policy gate (core/middleware.py)
# ---------------------------------------------------------------------------


def test_is_ingress_request_requires_both_id_and_name():
    assert is_ingress_request(_request(IDENTITY_HEADERS)) is True
    only_id = {k: v for k, v in IDENTITY_HEADERS.items() if k != "X-Remote-User-Name"}
    assert is_ingress_request(_request(only_id)) is False
    only_name = {k: v for k, v in IDENTITY_HEADERS.items() if k != "X-Remote-User-Id"}
    assert is_ingress_request(_request(only_name)) is False
    assert is_ingress_request(_request({})) is False


@pytest.mark.asyncio
async def test_frame_policy_relaxes_only_for_ingress_requests():
    # Proxied panel traffic is served SAMEORIGIN / frame-ancestors 'self'
    # (the HA panel iframe is same-origin); direct access keeps DENY / 'none'.
    async def _build(headers):
        application = FastAPI()
        application.add_middleware(SecurityHeadersMiddleware)

        @application.get("/")
        def index():
            return {"ok": True}

        return application

    with TestClient(await _build(IDENTITY_HEADERS)) as client:
        panel = client.get("/", headers=IDENTITY_HEADERS)
        assert panel.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in panel.headers["Content-Security-Policy"]
    with TestClient(await _build({})) as client:
        direct = client.get("/")
        assert direct.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in direct.headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# ingress_identity — the trust boundary
# ---------------------------------------------------------------------------


def test_ingress_identity_requires_enabled_feature():
    # Feature off (no env): never trust, regardless of peer.
    assert hass_auth.ingress_identity(_request(IDENTITY_HEADERS)) is None


def test_ingress_identity_trusts_supervisor_peer_only(monkeypatch):
    _enable_hass(monkeypatch, trusted_ip="172.30.32.2")
    ident = hass_auth.ingress_identity(_request(IDENTITY_HEADERS, client_host="172.30.32.2"))
    assert ident is not None
    assert ident[0] == "hauser"
    assert "system-users" in ident[1]
    # Same headers from any other peer (the directly exposed port) are
    # untrusted — they are client-spoofable there.
    spoofed = hass_auth.ingress_identity(_request(IDENTITY_HEADERS, client_host="192.0.2.10"))
    assert spoofed is None


def test_ingress_identity_requires_name_header(monkeypatch):
    _enable_hass(monkeypatch, trusted_ip="172.30.32.2")
    no_name = {k: v for k, v in IDENTITY_HEADERS.items() if k != "X-Remote-User-Name"}
    assert hass_auth.ingress_identity(_request(no_name, client_host="172.30.32.2")) is None


def test_ingress_identity_fails_closed_without_token(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_HASS_AUTH", "1")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    assert hass_auth.ingress_identity(_request(IDENTITY_HEADERS, client_host="172.30.32.2")) is None


# ---------------------------------------------------------------------------
# ensure_ha_user — provisioning policy
# ---------------------------------------------------------------------------


def test_ensure_ha_user_first_account_becomes_admin(tmp_path):
    manager = AuthManager(auth_path=str(tmp_path / "auth.json"))
    assert hass_auth.ensure_ha_user(manager, "HaUser", ha_admin=False) is True
    assert manager.is_admin("hauser")


def test_ensure_ha_user_maps_ha_admin_group(tmp_path):
    manager = AuthManager(auth_path=str(tmp_path / "auth.json"))
    manager.setup("root", "root-password-123")
    assert hass_auth.ensure_ha_user(manager, "HaAdmin", ha_admin=True) is True
    assert manager.is_admin("haadmin")
    assert hass_auth.ensure_ha_user(manager, "PlainUser", ha_admin=False) is True
    assert not manager.is_admin("plainuser")


def test_ensure_ha_user_refuses_reserved_names(tmp_path):
    manager = AuthManager(auth_path=str(tmp_path / "auth.json"))
    assert hass_auth.ensure_ha_user(manager, "system", ha_admin=True) is False
    assert "system" not in manager.users


# ---------------------------------------------------------------------------
# AuthMiddleware integration — session cookie issuance through the panel
# ---------------------------------------------------------------------------


def _build_auth_app(monkeypatch, tmp_path):
    import json

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse

    manager = AuthManager(auth_path=str(tmp_path / "auth.json"))
    manager.setup("localadmin", "local-password-123")
    # The middleware closes over the module-level manager.
    monkeypatch.setattr(app_module, "auth_manager", manager)

    async def _json_ok(request):
        return JSONResponse({"ok": True})

    application = Starlette()
    application.router.add_route("/api/models", _json_ok, methods=["GET"])
    application.add_middleware(app_module.AuthMiddleware)
    return application, manager


def test_panel_request_from_supervisor_signs_in_without_credentials(monkeypatch, tmp_path):
    _enable_hass(monkeypatch)  # no trusted-IP override: TestClient peer is "testclient"
    monkeypatch.setenv("ODYSSEUS_INGRESS_TRUSTED_IP", "testclient")
    application, manager = _build_auth_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        resp = client.get("/api/models", headers=IDENTITY_HEADERS)
        assert resp.status_code == 200
        assert SESSION_COOKIE in resp.cookies
    assert "hauser" in manager.users
    assert not manager.is_admin("hauser")  # regular HA user → regular account


def test_direct_port_cannot_spoof_identity_headers(monkeypatch, tmp_path):
    _enable_hass(monkeypatch)  # trusted IP stays unset → no peer matches
    application, manager = _build_auth_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        resp = client.get("/api/models", headers=IDENTITY_HEADERS)
        assert resp.status_code == 401
        assert SESSION_COOKIE not in resp.cookies
    assert "hauser" not in manager.users


def test_ha_admin_maps_to_odysseus_admin_through_panel(monkeypatch, tmp_path):
    _enable_hass(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_INGRESS_TRUSTED_IP", "testclient")
    application, manager = _build_auth_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        resp = client.get("/api/models", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
    assert "haadmin" in manager.users
    assert manager.is_admin("haadmin")