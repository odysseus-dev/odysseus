import io
import json
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    file_id = "a" * 32 + ".png"
    image_path = upload_dir / file_id
    Image.new("RGB", (640, 360), "red").save(image_path)
    (upload_dir / "uploads.json").write_text(
        json.dumps({"alice:image": {
            "id": file_id,
            "path": str(image_path),
            "name": "image.png",
            "mime": "image/png",
            "owner": "alice",
        }}),
        encoding="utf-8",
    )
    cache_dir = upload_dir / ".vision"
    cache_dir.mkdir()
    cache_path = cache_dir / f"{file_id}.txt"
    cache_path.write_text("A red image", encoding="utf-8")
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )

    @app.middleware("http")
    async def set_user(request: Request, call_next):
        user = request.headers.get("x-test-user")
        if user:
            request.state.current_user = user
        if request.headers.get("x-test-api-token"):
            request.state.current_user = "api"
            request.state.api_token = True
            request.state.api_token_owner = request.headers.get("x-test-api-token-owner")
            scopes_header = request.headers.get("x-test-api-token-scopes")
            if scopes_header is not None:
                request.state.api_token_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
            else:
                request.state.api_token_scopes = ["chat"]
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        yield client, file_id, image_path, cache_path


@pytest.mark.parametrize("auth_enabled", ["false", " false ", "true"])
@pytest.mark.parametrize("operation", ["download", "thumbnail", "read_vision", "edit_vision"])
def test_upload_access_after_disabling_login(upload_client, monkeypatch, auth_enabled, operation):
    monkeypatch.setenv("AUTH_ENABLED", auth_enabled)
    client, file_id, image_path, cache_path = upload_client
    url = f"/api/upload/{file_id}"
    if operation == "edit_vision":
        response = client.put(f"{url}/vision", json={"text": "Edited description"})
    elif operation == "read_vision":
        response = client.get(f"{url}/vision")
    else:
        response = client.get(url, params={"thumb": int(operation == "thumbnail")})

    if auth_enabled.strip() == "true":
        assert response.status_code == 403
        assert cache_path.read_text(encoding="utf-8") == "A red image"
        return

    assert response.status_code == 200, response.text
    if operation == "download":
        assert response.content == image_path.read_bytes()
    elif operation == "thumbnail":
        assert response.headers["content-type"] == "image/jpeg"
        assert (image_path.parent / ".thumbs" / f"{file_id}.jpg").is_file()
    elif operation == "read_vision":
        assert response.json() == {"text": "A red image", "cached": True}
    else:
        assert response.json() == {"ok": True}
        assert cache_path.read_text(encoding="utf-8") == "Edited description"


# ===========================================================================
# 1. AUTH_ENABLED=true with auth_manager.is_configured=False
# ===========================================================================


@pytest.mark.parametrize("operation", ["download", "thumbnail", "read_vision", "edit_vision"])
def test_unconfigured_auth_manager_fails_closed(upload_client, monkeypatch, operation):
    """When AUTH_ENABLED=true and auth_manager is not configured (e.g. first-run
    before initial setup is complete), existing uploads must NOT be exposed
    and modification attempts must be rejected with 401 Setup required."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, cache_path = upload_client
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=False,
        is_admin=lambda user: False,
    )
    url = f"/api/upload/{file_id}"

    if operation == "edit_vision":
        response = client.put(f"{url}/vision", json={"text": "Defaced OCR"})
    elif operation == "read_vision":
        response = client.get(f"{url}/vision")
    else:
        response = client.get(url, params={"thumb": int(operation == "thumbnail")})

    assert response.status_code == 401, response.text
    if operation == "edit_vision":
        assert cache_path.read_text(encoding="utf-8") == "A red image"


@pytest.mark.parametrize("caller,expected_status", [
    (None, 403),        # Anonymous caller is rejected with 403 Access denied
    ("bob", 404),       # Non-owner non-admin caller is rejected with 404 File not found
    ("alice", 200),     # Legitimate owner is allowed
    ("admin", 200),     # Admin user is allowed
])
def test_auth_configured_enforces_owner_isolation(upload_client, monkeypatch, caller, expected_status):
    """When AUTH_ENABLED=true and auth_manager IS configured, owner isolation
    must be strictly enforced: anonymous callers get 403, cross-user callers
    get 404, while the owner and admin are permitted."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, _ = upload_client
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )
    headers = {"x-test-user": caller} if caller else {}

    response = client.get(f"/api/upload/{file_id}", headers=headers)
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.content == image_path.read_bytes()


# ===========================================================================
# 2. Finding A: Unconfigured manager in route state must NOT grant access
# ===========================================================================


@pytest.mark.parametrize("auth_method", ["cookie", "bearer"])
@pytest.mark.parametrize("operation", ["download", "thumbnail", "read_vision", "edit_vision"])
def test_finding_a_unconfigured_route_manager_denies_cross_user(upload_client, monkeypatch, auth_method, operation):
    """Finding A Regression Test:
    When AUTH_ENABLED=true and middleware authenticates Bob, but app.state.auth_manager
    is an unconfigured manager instance (is_configured=False), Bob MUST NOT be granted
    access to Alice's upload. Access must fail closed with 401 Setup required."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, cache_path = upload_client

    # Route observes an unconfigured manager
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=False,
        is_admin=lambda user: False,
    )

    url = f"/api/upload/{file_id}"
    headers = {}
    if auth_method == "cookie":
        headers["x-test-user"] = "bob"
    else:
        # Bearer token owned by Bob
        headers["x-test-api-token"] = "1"
        headers["x-test-api-token-owner"] = "bob"

    if operation == "edit_vision":
        response = client.put(f"{url}/vision", headers=headers, json={"text": "Defaced by Bob"})
    elif operation == "read_vision":
        response = client.get(f"{url}/vision", headers=headers)
    else:
        response = client.get(url, headers=headers, params={"thumb": int(operation == "thumbnail")})

    # Must fail closed: 401 Setup required (never 200)
    assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
    if operation == "edit_vision":
        # OCR cache must remain untouched
        assert cache_path.read_text(encoding="utf-8") == "A red image"


# ===========================================================================
# 3. Finding B: Loopback requests under infrastructure failure must fail closed
# ===========================================================================


@pytest.mark.parametrize("loopback_host", ["127.0.0.1", "::1", "localhost"])
@pytest.mark.parametrize("proxy_header", [None, "X-Forwarded-For", "CF-Connecting-IP"])
@pytest.mark.parametrize("caller", [None, "bob", "admin"])
def test_finding_b_loopback_infrastructure_failure_fails_closed(
    upload_client, monkeypatch, loopback_host, proxy_header, caller
):
    """Finding B Regression Test:
    When AUTH_ENABLED=true and app.state.auth_manager is None (infrastructure failure),
    loopback requests (127.0.0.1, ::1, localhost) MUST NOT bypass authentication or
    gain access to uploads. All requests must fail closed with 503 Service Unavailable."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, _, cache_path = upload_client
    if hasattr(client.app.state, "auth_manager"):
        delattr(client.app.state, "auth_manager")

    headers = {}
    if caller:
        headers["x-test-user"] = caller
    if proxy_header:
        headers[proxy_header] = "203.0.113.195"

    url = f"/api/upload/{file_id}"

    # Test download
    resp_dl = client.get(url, headers=headers)
    assert resp_dl.status_code == 503, f"Loopback host {loopback_host} leaked file: {resp_dl.status_code}"

    # Test thumbnail
    resp_th = client.get(url, headers=headers, params={"thumb": 1})
    assert resp_th.status_code == 503

    # Test vision read
    resp_vr = client.get(f"{url}/vision", headers=headers)
    assert resp_vr.status_code == 503

    # Test vision edit
    resp_ve = client.put(f"{url}/vision", headers=headers, json={"text": "Defaced"})
    assert resp_ve.status_code == 503
    assert cache_path.read_text(encoding="utf-8") == "A red image"


# ===========================================================================
# 4. Absent auth_manager (infrastructure failure) fail-closed behavior
# ===========================================================================


@pytest.mark.parametrize("operation", ["download", "thumbnail", "read_vision", "edit_vision"])
def test_absent_auth_manager_fails_closed(upload_client, monkeypatch, operation):
    """When AUTH_ENABLED=true and auth_manager is absent from app.state (unexpected
    infrastructure failure), upload routes must fail closed (503 Service Unavailable)
    rather than failing open."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, cache_path = upload_client
    if hasattr(client.app.state, "auth_manager"):
        delattr(client.app.state, "auth_manager")

    url = f"/api/upload/{file_id}"
    if operation == "edit_vision":
        response = client.put(f"{url}/vision", json={"text": "No auth_manager edit"})
    elif operation == "read_vision":
        response = client.get(f"{url}/vision")
    else:
        response = client.get(url, params={"thumb": int(operation == "thumbnail")})

    assert response.status_code == 503, response.text
    if operation == "edit_vision":
        assert cache_path.read_text(encoding="utf-8") == "A red image"


def test_absent_auth_manager_restoring_auth_enforces_access(upload_client, monkeypatch):
    """Verify that when auth_manager is absent, routes fail closed (503),
    and attaching a configured auth_manager enforces full owner isolation."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, _, _ = upload_client

    # First, verify access fails closed when auth_manager is absent
    if hasattr(client.app.state, "auth_manager"):
        delattr(client.app.state, "auth_manager")
    assert client.get(f"/api/upload/{file_id}").status_code == 503

    # Now attach configured auth_manager: anonymous access must fail closed (403),
    # wrong owner gets 404, owner and admin get 200.
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )
    assert client.get(f"/api/upload/{file_id}").status_code == 403
    assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "bob"}).status_code == 404
    assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "alice"}).status_code == 200
    assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "admin"}).status_code == 200


def test_authenticated_cross_user_with_absent_auth_manager_fails_closed(upload_client, monkeypatch):
    """When AUTH_ENABLED=true, an authenticated non-owner (e.g. Bob) must NOT be able
    to access Alice's upload when app.state.auth_manager is absent/None.
    The route must fail closed (503 Service Unavailable)."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, _, cache_path = upload_client
    if hasattr(client.app.state, "auth_manager"):
        delattr(client.app.state, "auth_manager")

    # Bob is authenticated but auth_manager is absent on app.state -> 503
    resp_download = client.get(f"/api/upload/{file_id}", headers={"x-test-user": "bob"})
    assert resp_download.status_code == 503

    resp_thumb = client.get(f"/api/upload/{file_id}?thumb=1", headers={"x-test-user": "bob"})
    assert resp_thumb.status_code == 503

    resp_read_vision = client.get(f"/api/upload/{file_id}/vision", headers={"x-test-user": "bob"})
    assert resp_read_vision.status_code == 503

    resp_edit_vision = client.put(f"/api/upload/{file_id}/vision", headers={"x-test-user": "bob"}, json={"text": "Defaced"})
    assert resp_edit_vision.status_code == 503
    assert cache_path.read_text(encoding="utf-8") == "A red image"


def test_absent_auth_manager_allows_access_when_auth_disabled(upload_client, monkeypatch):
    """When AUTH_ENABLED=false, absent auth_manager must NOT block single-user access."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client, file_id, image_path, cache_path = upload_client
    if hasattr(client.app.state, "auth_manager"):
        delattr(client.app.state, "auth_manager")

    assert client.get(f"/api/upload/{file_id}").status_code == 200
    assert client.get(f"/api/upload/{file_id}?thumb=1").status_code == 200
    assert client.get(f"/api/upload/{file_id}/vision").status_code == 200
    assert client.put(f"/api/upload/{file_id}/vision", json={"text": "Single user edit"}).status_code == 200
    assert cache_path.read_text(encoding="utf-8") == "Single user edit"


# ===========================================================================
# 5. Bearer token identity & Legacy ownerless upload isolation
# ===========================================================================


def test_bearer_token_owner_isolation(upload_client, monkeypatch):
    """Bearer tokens must resolve to their actual token owner (effective_user).
    Alice's token gets 200 for Alice's upload, Bob's token gets 404, Admin's token
    gets 404 (delegated tokens never inherit admin privileges on other users' files)."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, cache_path = upload_client
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )

    # Bob's bearer token -> 404 (non-owner)
    bob_headers = {"x-test-api-token": "1", "x-test-api-token-owner": "bob"}
    assert client.get(f"/api/upload/{file_id}", headers=bob_headers).status_code == 404
    assert client.get(f"/api/upload/{file_id}?thumb=1", headers=bob_headers).status_code == 404
    assert client.get(f"/api/upload/{file_id}/vision", headers=bob_headers).status_code == 404
    resp_edit = client.put(f"/api/upload/{file_id}/vision", headers=bob_headers, json={"text": "Defaced by Bob token"})
    assert resp_edit.status_code == 404
    assert cache_path.read_text(encoding="utf-8") == "A red image"

    # Alice's bearer token -> 200 (legitimate owner)
    alice_headers = {"x-test-api-token": "1", "x-test-api-token-owner": "alice"}
    assert client.get(f"/api/upload/{file_id}", headers=alice_headers).status_code == 200
    assert client.get(f"/api/upload/{file_id}?thumb=1", headers=alice_headers).status_code == 200
    assert client.get(f"/api/upload/{file_id}/vision", headers=alice_headers).status_code == 200
    resp_edit_alice = client.put(f"/api/upload/{file_id}/vision", headers=alice_headers, json={"text": "Updated by Alice token"})
    assert resp_edit_alice.status_code == 200
    assert cache_path.read_text(encoding="utf-8") == "Updated by Alice token"

    # Admin's bearer token accessing Alice's file -> 404 (delegated token does NOT inherit admin access)
    admin_headers = {"x-test-api-token": "1", "x-test-api-token-owner": "admin"}
    assert client.get(f"/api/upload/{file_id}", headers=admin_headers).status_code == 404
    assert client.get(f"/api/upload/{file_id}?thumb=1", headers=admin_headers).status_code == 404
    assert client.get(f"/api/upload/{file_id}/vision", headers=admin_headers).status_code == 404
    resp_edit_admin = client.put(f"/api/upload/{file_id}/vision", headers=admin_headers, json={"text": "Defaced by Admin token"})
    assert resp_edit_admin.status_code == 404
    assert cache_path.read_text(encoding="utf-8") == "Updated by Alice token"

    # Interactive Admin session (cookie) accessing Alice's file -> 200
    admin_cookie_headers = {"x-test-user": "admin"}
    assert client.get(f"/api/upload/{file_id}", headers=admin_cookie_headers).status_code == 200
    assert client.get(f"/api/upload/{file_id}?thumb=1", headers=admin_cookie_headers).status_code == 200
    assert client.get(f"/api/upload/{file_id}/vision", headers=admin_cookie_headers).status_code == 200
    resp_edit_admin_cookie = client.put(f"/api/upload/{file_id}/vision", headers=admin_cookie_headers, json={"text": "Admin override"})
    assert resp_edit_admin_cookie.status_code == 200
    assert cache_path.read_text(encoding="utf-8") == "Admin override"


def test_legacy_ownerless_upload_isolation(tmp_path, monkeypatch):
    """Legacy ownerless uploads (owner=None in uploads.json) must remain
    administrator-only for interactive sessions when AUTH_ENABLED=true:
    - Anonymous callers get 403 Access denied
    - Regular authenticated users get 404 File not found
    - Delegated bearer tokens (even admin-owned) get 404 File not found
    - Verified interactive administrators get 200 OK"""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    monkeypatch.setenv("AUTH_ENABLED", "true")
    upload_dir = tmp_path / "uploads_legacy"
    upload_dir.mkdir()
    file_id = "f" * 32 + ".png"
    img_path = upload_dir / file_id
    Image.new("RGB", (100, 100), "blue").save(img_path)
    (upload_dir / "uploads.json").write_text(
        json.dumps({"legacy_key": {
            "id": file_id,
            "path": str(img_path),
            "name": "legacy.png",
            "mime": "image/png",
            "owner": None,
        }}),
        encoding="utf-8",
    )
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )

    @app.middleware("http")
    async def set_user(request: Request, call_next):
        user = request.headers.get("x-test-user")
        if user:
            request.state.current_user = user
        if request.headers.get("x-test-api-token"):
            request.state.current_user = "api"
            request.state.api_token = True
            request.state.api_token_owner = request.headers.get("x-test-api-token-owner")
            scopes_header = request.headers.get("x-test-api-token-scopes")
            if scopes_header is not None:
                request.state.api_token_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
            else:
                request.state.api_token_scopes = ["chat"]
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        # Anonymous -> 403
        assert client.get(f"/api/upload/{file_id}").status_code == 403
        assert client.get(f"/api/upload/{file_id}?thumb=1").status_code == 403
        assert client.get(f"/api/upload/{file_id}/vision").status_code == 403

        # Non-admin user "bob" (interactive cookie) -> 404
        assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "bob"}).status_code == 404
        assert client.get(f"/api/upload/{file_id}?thumb=1", headers={"x-test-user": "bob"}).status_code == 404
        assert client.get(f"/api/upload/{file_id}/vision", headers={"x-test-user": "bob"}).status_code == 404

        # Non-admin bearer token "bob" -> 404
        bob_bearer = {"x-test-api-token": "1", "x-test-api-token-owner": "bob"}
        assert client.get(f"/api/upload/{file_id}", headers=bob_bearer).status_code == 404

        # Admin bearer token -> 404 (delegated token never inherits admin privileges on ownerless files)
        admin_bearer = {"x-test-api-token": "1", "x-test-api-token-owner": "admin", "x-test-api-token-scopes": "chat"}
        assert client.get(f"/api/upload/{file_id}", headers=admin_bearer).status_code == 404
        assert client.get(f"/api/upload/{file_id}?thumb=1", headers=admin_bearer).status_code == 404
        assert client.get(f"/api/upload/{file_id}/vision", headers=admin_bearer).status_code == 404
        assert client.put(f"/api/upload/{file_id}/vision", headers=admin_bearer, json={"text": "Defaced"}).status_code == 404

        # Admin user "admin" (interactive cookie) -> 200
        assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "admin"}).status_code == 200
        assert client.get(f"/api/upload/{file_id}?thumb=1", headers={"x-test-user": "admin"}).status_code == 200
        assert client.get(f"/api/upload/{file_id}/vision", headers={"x-test-user": "admin"}).status_code == 200


def test_delegated_token_can_access_own_file_when_owner_is_admin(tmp_path, monkeypatch):
    """When a file is owned by 'admin', an admin-owned bearer token with valid scope
    can access and edit it, but a non-admin token or user cannot."""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    monkeypatch.setenv("AUTH_ENABLED", "true")
    upload_dir = tmp_path / "uploads_admin_owned"
    upload_dir.mkdir()
    file_id = "e" * 32 + ".png"
    img_path = upload_dir / file_id
    Image.new("RGB", (100, 100), "green").save(img_path)
    (upload_dir / "uploads.json").write_text(
        json.dumps({"admin_key": {
            "id": file_id,
            "path": str(img_path),
            "name": "admin_file.png",
            "mime": "image/png",
            "owner": "admin",
        }}),
        encoding="utf-8",
    )
    cache_dir = upload_dir / ".vision"
    cache_dir.mkdir()
    cache_path = cache_dir / f"{file_id}.txt"
    cache_path.write_text("Admin green image", encoding="utf-8")

    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )

    @app.middleware("http")
    async def set_user(request: Request, call_next):
        user = request.headers.get("x-test-user")
        if user:
            request.state.current_user = user
        if request.headers.get("x-test-api-token"):
            request.state.current_user = "api"
            request.state.api_token = True
            request.state.api_token_owner = request.headers.get("x-test-api-token-owner")
            scopes_header = request.headers.get("x-test-api-token-scopes")
            if scopes_header is not None:
                request.state.api_token_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
            else:
                request.state.api_token_scopes = ["chat"]
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        # Admin's bearer token accessing its own file -> 200
        admin_bearer = {"x-test-api-token": "1", "x-test-api-token-owner": "admin", "x-test-api-token-scopes": "chat"}
        assert client.get(f"/api/upload/{file_id}", headers=admin_bearer).status_code == 200
        assert client.get(f"/api/upload/{file_id}/vision", headers=admin_bearer).status_code == 200
        resp_put = client.put(f"/api/upload/{file_id}/vision", headers=admin_bearer, json={"text": "Admin updated own"})
        assert resp_put.status_code == 200
        assert cache_path.read_text(encoding="utf-8") == "Admin updated own"

        # Bob's bearer token accessing admin's file -> 404
        bob_bearer = {"x-test-api-token": "1", "x-test-api-token-owner": "bob", "x-test-api-token-scopes": "chat"}
        assert client.get(f"/api/upload/{file_id}", headers=bob_bearer).status_code == 404
        assert client.put(f"/api/upload/{file_id}/vision", headers=bob_bearer, json={"text": "Bob deface"}).status_code == 404
        assert cache_path.read_text(encoding="utf-8") == "Admin updated own"

        # Bob's interactive session accessing admin's file -> 404
        assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "bob"}).status_code == 404


@pytest.mark.parametrize("scope,can_read,can_write", [
    ("chat", True, True),
    ("documents:write", True, True),
    ("documents:read", True, False),
    ("todos:read", False, False),
    ("todos:write", False, False),
    ("", False, False),
])
def test_delegated_token_scope_matrix(upload_client, monkeypatch, scope, can_read, can_write):
    """Test token scope enforcement on legitimate owner's upload (Alice's file):
    - chat and documents:write allow both read and write
    - documents:read allows read but rejects write with 403
    - unrelated scopes (todos:read, todos:write) or empty scopes reject with 403
    - on rejected write, persisted OCR content remains untouched."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, cache_path = upload_client
    initial_text = cache_path.read_text(encoding="utf-8")

    headers = {
        "x-test-api-token": "1",
        "x-test-api-token-owner": "alice",
        "x-test-api-token-scopes": scope,
    }

    # Test read operations
    resp_dl = client.get(f"/api/upload/{file_id}", headers=headers)
    resp_th = client.get(f"/api/upload/{file_id}?thumb=1", headers=headers)
    resp_vr = client.get(f"/api/upload/{file_id}/vision", headers=headers)

    if can_read:
        assert resp_dl.status_code == 200
        assert resp_th.status_code == 200
        assert resp_vr.status_code == 200
    else:
        assert resp_dl.status_code == 403
        assert resp_th.status_code == 403
        assert resp_vr.status_code == 403

    # Test write operation (OCR edit)
    resp_ve = client.put(f"/api/upload/{file_id}/vision", headers=headers, json={"text": f"Edit by {scope}"})
    if can_write:
        assert resp_ve.status_code == 200
        assert cache_path.read_text(encoding="utf-8") == f"Edit by {scope}"
    else:
        assert resp_ve.status_code == 403
        # Persisted content must remain completely unchanged
        assert cache_path.read_text(encoding="utf-8") == initial_text


@pytest.mark.parametrize("sentinel_owner", ["api", "internal-tool", "demo", "system", ""])
def test_delegated_token_sentinel_or_missing_owner_denied(upload_client, monkeypatch, sentinel_owner):
    """Delegated tokens with sentinel owner names or missing owner must be rejected with 403."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, _, cache_path = upload_client
    initial_text = cache_path.read_text(encoding="utf-8")

    headers = {"x-test-api-token": "1", "x-test-api-token-scopes": "chat"}
    if sentinel_owner:
        headers["x-test-api-token-owner"] = sentinel_owner

    resp_dl = client.get(f"/api/upload/{file_id}", headers=headers)
    assert resp_dl.status_code == 403

    resp_ve = client.put(f"/api/upload/{file_id}/vision", headers=headers, json={"text": "Defaced by sentinel"})
    assert resp_ve.status_code == 403
    assert cache_path.read_text(encoding="utf-8") == initial_text


@pytest.mark.parametrize("sentinel_user", ["api", "internal-tool", "demo", "system"])
def test_interactive_sentinel_user_denied(upload_client, monkeypatch, sentinel_user):
    """Interactive requests claiming sentinel user identities must be rejected with 403."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, _, cache_path = upload_client

    headers = {"x-test-user": sentinel_user}
    assert client.get(f"/api/upload/{file_id}", headers=headers).status_code == 403
    assert client.put(f"/api/upload/{file_id}/vision", headers=headers, json={"text": "Defaced"}).status_code == 403
    assert cache_path.read_text(encoding="utf-8") == "A red image"


def test_upload_post_delegated_token_authorization(tmp_path, monkeypatch):
    """POST /api/upload with AUTH_ENABLED=true and delegated bearer tokens:
    - Token with 'chat' or 'documents:write' succeeds and sets file owner to token owner
    - Token with 'documents:read' is rejected with 403
    - Token with 'todos:read' is rejected with 403
    - Token with missing owner is rejected with 403
    - Token with sentinel owner ('api') is rejected with 403"""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    monkeypatch.setenv("AUTH_ENABLED", "true")
    upload_dir = tmp_path / "uploads_post_token"
    upload_dir.mkdir()
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )

    @app.middleware("http")
    async def set_user(request: Request, call_next):
        user = request.headers.get("x-test-user")
        if user:
            request.state.current_user = user
        if request.headers.get("x-test-api-token"):
            request.state.current_user = "api"
            request.state.api_token = True
            request.state.api_token_owner = request.headers.get("x-test-api-token-owner")
            scopes_header = request.headers.get("x-test-api-token-scopes")
            if scopes_header is not None:
                request.state.api_token_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
            else:
                request.state.api_token_scopes = ["chat"]
        return await call_next(request)

    app.include_router(router)

    with TestClient(app) as client:
        # 1. Valid token with 'chat' scope
        resp_chat = client.post(
            "/api/upload",
            files=[("files", ("chat.txt", io.BytesIO(b"chat content"), "text/plain"))],
            headers={"x-test-api-token": "1", "x-test-api-token-owner": "alice", "x-test-api-token-scopes": "chat"},
        )
        assert resp_chat.status_code == 200
        chat_id = resp_chat.json()["files"][0]["id"]
        index = handler._load_upload_index()
        rec_chat = next(v for v in index.values() if v.get("id") == chat_id)
        assert rec_chat["owner"] == "alice"

        # 2. Valid token with 'documents:write' scope
        resp_doc = client.post(
            "/api/upload",
            files=[("files", ("doc.txt", io.BytesIO(b"doc content"), "text/plain"))],
            headers={"x-test-api-token": "1", "x-test-api-token-owner": "bob", "x-test-api-token-scopes": "documents:write"},
        )
        assert resp_doc.status_code == 200
        doc_id = resp_doc.json()["files"][0]["id"]
        index = handler._load_upload_index()
        rec_doc = next(v for v in index.values() if v.get("id") == doc_id)
        assert rec_doc["owner"] == "bob"

        # 3. Token with read-only 'documents:read' scope -> 403
        resp_ro = client.post(
            "/api/upload",
            files=[("files", ("ro.txt", io.BytesIO(b"ro content"), "text/plain"))],
            headers={"x-test-api-token": "1", "x-test-api-token-owner": "alice", "x-test-api-token-scopes": "documents:read"},
        )
        assert resp_ro.status_code == 403

        # 4. Token with unrelated 'todos:read' scope -> 403
        resp_unrelated = client.post(
            "/api/upload",
            files=[("files", ("unrelated.txt", io.BytesIO(b"content"), "text/plain"))],
            headers={"x-test-api-token": "1", "x-test-api-token-owner": "alice", "x-test-api-token-scopes": "todos:read"},
        )
        assert resp_unrelated.status_code == 403

        # 5. Token with missing owner -> 403
        resp_no_owner = client.post(
            "/api/upload",
            files=[("files", ("no_owner.txt", io.BytesIO(b"content"), "text/plain"))],
            headers={"x-test-api-token": "1", "x-test-api-token-scopes": "chat"},
        )
        assert resp_no_owner.status_code == 403

        # 6. Token with sentinel owner 'api' -> 403
        resp_sentinel = client.post(
            "/api/upload",
            files=[("files", ("sentinel.txt", io.BytesIO(b"content"), "text/plain"))],
            headers={"x-test-api-token": "1", "x-test-api-token-owner": "api", "x-test-api-token-scopes": "chat"},
        )
        assert resp_sentinel.status_code == 403


# ===========================================================================
# 3. POST /api/upload with AUTH_ENABLED=false
# ===========================================================================


def test_upload_post_succeeds_without_login(tmp_path, monkeypatch):
    """POST /api/upload with AUTH_ENABLED=false succeeds without authentication,
    persists the file, stores owner=None, and can be retrieved via GET."""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    monkeypatch.setenv("AUTH_ENABLED", "false")
    upload_dir = tmp_path / "uploads_post"
    upload_dir.mkdir()
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(router)

    file_bytes = b"Hello, no-login upload test content!"
    with TestClient(app) as client:
        response = client.post(
            "/api/upload",
            files=[("files", ("test_file.txt", io.BytesIO(file_bytes), "text/plain"))],
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert "files" in data
        assert len(data["files"]) == 1
        item = data["files"][0]
        assert item["name"] == "test_file.txt"
        assert item["size"] == len(file_bytes)
        new_id = item["id"]

        # Check upload index: owner must be None (no authenticated user)
        index = handler._load_upload_index()
        record = next((v for v in index.values() if v.get("id") == new_id), None)
        assert record is not None
        assert record.get("owner") is None

        # Verify storage isolation: file path is confined inside upload_dir
        saved_path = record.get("path")
        assert saved_path and handler.inside_base_dir(saved_path)

        # Verify download of the newly uploaded file succeeds
        download_resp = client.get(f"/api/upload/{new_id}")
        assert download_resp.status_code == 200
        assert download_resp.content == file_bytes


def test_upload_post_preserves_validations_and_constraints(tmp_path, monkeypatch):
    """POST /api/upload with AUTH_ENABLED=false continues enforcing validation:
    empty files, oversized files, and dangerous file types are rejected."""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    monkeypatch.setenv("AUTH_ENABLED", "false")
    upload_dir = tmp_path / "uploads_val"
    upload_dir.mkdir()
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(router)

    with TestClient(app) as client:
        # 1. Empty file rejection
        resp_empty = client.post(
            "/api/upload",
            files=[("files", ("empty.txt", io.BytesIO(b""), "text/plain"))],
        )
        assert resp_empty.status_code == 400

        # 2. Oversized file rejection
        handler.max_upload_size = 10  # 10 bytes limit
        resp_oversized = client.post(
            "/api/upload",
            files=[("files", ("large.txt", io.BytesIO(b"x" * 50), "text/plain"))],
        )
        assert resp_oversized.status_code == 400

        # 3. Dangerous file type rejection (executable)
        resp_dangerous = client.post(
            "/api/upload",
            files=[("files", ("malware.exe", io.BytesIO(b"MZ123"), "application/x-msdownload"))],
        )
        assert resp_dangerous.status_code == 400


def test_upload_post_sanitizes_filename_and_confines_storage(tmp_path, monkeypatch):
    """POST /api/upload with AUTH_ENABLED=false sanitizes path traversal attempts
    in filenames and confines saved files inside the upload directory."""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    monkeypatch.setenv("AUTH_ENABLED", "false")
    upload_dir = tmp_path / "uploads_trav"
    upload_dir.mkdir()
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(router)

    with TestClient(app) as client:
        resp_traversal = client.post(
            "/api/upload",
            files=[("files", ("../../traversal.txt", io.BytesIO(b"safe content"), "text/plain"))],
        )
        assert resp_traversal.status_code == 200
        traversal_id = resp_traversal.json()["files"][0]["id"]
        index = handler._load_upload_index()
        rec = next((v for v in index.values() if v.get("id") == traversal_id), None)
        assert rec is not None
        assert handler.inside_base_dir(rec["path"])
        assert "/" not in rec["name"] and "\\" not in rec["name"]


def test_upload_post_no_login_then_auth_enabled_prevents_unauthorized_download(tmp_path, monkeypatch):
    """When a file is uploaded in AUTH_ENABLED=false mode (stored with owner=None),
    and the server subsequently runs with AUTH_ENABLED=true and auth configured,
    unauthorized downloads are prevented: anonymous callers get 403, non-admin
    users get 404, and only admins can access."""
    from routes import upload_routes
    from src.upload_handler import UploadHandler

    # Step 1: Upload in no-login mode
    monkeypatch.setenv("AUTH_ENABLED", "false")
    upload_dir = tmp_path / "uploads_transition"
    upload_dir.mkdir()
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )

    @app.middleware("http")
    async def set_user(request: Request, call_next):
        user = request.headers.get("x-test-user")
        if user:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(router)

    file_bytes = b"Sensitive document uploaded in no-login mode"
    with TestClient(app) as client:
        upload_resp = client.post(
            "/api/upload",
            files=[("files", ("document.txt", io.BytesIO(file_bytes), "text/plain"))],
        )
        assert upload_resp.status_code == 200
        file_id = upload_resp.json()["files"][0]["id"]

        # Step 2: Transition to AUTH_ENABLED=true
        monkeypatch.setenv("AUTH_ENABLED", "true")

        # Anonymous caller -> 403 Access denied
        resp_anon = client.get(f"/api/upload/{file_id}")
        assert resp_anon.status_code == 403

        # Non-admin user "bob" -> 404 File not found (owner isolation)
        resp_bob = client.get(f"/api/upload/{file_id}", headers={"x-test-user": "bob"})
        assert resp_bob.status_code == 404

        # Admin user "admin" -> 200 OK
        resp_admin = client.get(f"/api/upload/{file_id}", headers={"x-test-user": "admin"})
        assert resp_admin.status_code == 200
        assert resp_admin.content == file_bytes
