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
# 1. AUTH_ENABLED=true with auth_manager.is_configured=False (First-run mode)
# ===========================================================================


@pytest.mark.parametrize("operation", ["download", "thumbnail", "read_vision", "edit_vision"])
def test_first_run_mode_unconfigured_auth_manager_allows_access(upload_client, monkeypatch, operation):
    """When AUTH_ENABLED=true but auth_manager is not yet configured (first-run),
    the owner gate must be skipped and all operations must succeed."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, image_path, cache_path = upload_client
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=False,
        is_admin=lambda user: False,
    )
    url = f"/api/upload/{file_id}"

    if operation == "edit_vision":
        response = client.put(f"{url}/vision", json={"text": "First-run vision edit"})
    elif operation == "read_vision":
        response = client.get(f"{url}/vision")
    else:
        response = client.get(url, params={"thumb": int(operation == "thumbnail")})

    assert response.status_code == 200, response.text
    if operation == "download":
        assert response.content == image_path.read_bytes()
    elif operation == "thumbnail":
        assert response.headers["content-type"] == "image/jpeg"
    elif operation == "read_vision":
        assert response.json() == {"text": "A red image", "cached": True}
    elif operation == "edit_vision":
        assert response.json() == {"ok": True}
        assert cache_path.read_text(encoding="utf-8") == "First-run vision edit"


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
# 2. auth_manager absent from app.state
# ===========================================================================


@pytest.mark.parametrize("operation", ["download", "thumbnail", "read_vision", "edit_vision"])
def test_absent_auth_manager_allows_access_without_crash(upload_client, monkeypatch, operation):
    """When auth_manager is absent from app.state (e.g. standalone test or
    minimal configuration), upload routes must not crash (no 500) and must
    serve requests without error."""
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

    assert response.status_code == 200, response.text
    assert response.status_code != 500
    if operation == "download":
        assert response.content == image_path.read_bytes()
    elif operation == "edit_vision":
        assert response.json() == {"ok": True}
        assert cache_path.read_text(encoding="utf-8") == "No auth_manager edit"


def test_absent_auth_manager_restoring_auth_closes_access(upload_client, monkeypatch):
    """Verify that absent auth_manager is not an irreversible authorization bypass:
    attaching a configured auth_manager immediately restores owner isolation."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client, file_id, _, _ = upload_client

    # First, verify access works when auth_manager is absent
    if hasattr(client.app.state, "auth_manager"):
        delattr(client.app.state, "auth_manager")
    assert client.get(f"/api/upload/{file_id}").status_code == 200

    # Now attach configured auth_manager: anonymous access must immediately fail closed
    client.app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: user == "admin",
    )
    assert client.get(f"/api/upload/{file_id}").status_code == 403
    assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "bob"}).status_code == 404
    assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "alice"}).status_code == 200
    assert client.get(f"/api/upload/{file_id}", headers={"x-test-user": "admin"}).status_code == 200


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
