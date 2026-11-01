"""Tests for the HTTP GET and HEAD contract on /api/upload/{file_id}.

Verifies that HEAD /api/upload/{file_id} returns identical status and response
headers to GET while transferring zero body bytes, enforcing the same
authorization and ownership checks.
"""
import json
import os
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _make_upload_env(tmp_path, monkeypatch):
    from src.upload_handler import UploadHandler
    from src import constants
    import routes.upload_routes as upload_routes_mod

    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "06" / "02"
    dated.mkdir(parents=True)

    alice_id = "a" * 32 + ".png"
    bob_id = "b" * 32 + ".png"
    alice_path = dated / alice_id
    bob_path = dated / bob_id
    alice_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    bob_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x02\x08\x06\x00\x00\x00\x1f\x15c4"
    alice_path.write_bytes(alice_bytes)
    bob_path.write_bytes(bob_bytes)

    index = {
        f"alice:{alice_id}": {
            "id": alice_id,
            "path": str(alice_path),
            "mime": "image/png",
            "size": len(alice_bytes),
            "name": "alice.png",
            "original_name": "alice.png",
            "owner": "alice",
        },
        f"bob:{bob_id}": {
            "id": bob_id,
            "path": str(bob_path),
            "mime": "image/png",
            "size": len(bob_bytes),
            "name": "bob.png",
            "original_name": "bob.png",
            "owner": "bob",
        },
    }
    (upload_dir / "uploads.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(upload_dir))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    return handler, alice_id, bob_id, upload_dir, alice_bytes


def _create_test_app(handler, current_user="alice", auth_manager=None):
    from routes.upload_routes import router, setup_upload_routes

    # Fresh router instance to avoid route collisions across test calls
    from fastapi import APIRouter
    test_router = APIRouter(prefix="/api/upload", tags=["uploads"])
    import routes.upload_routes as ur
    orig_router = ur.router
    ur.router = test_router
    try:
        setup_upload_routes(handler)
    finally:
        ur.router = orig_router

    app = FastAPI()
    app.state.auth_manager = auth_manager or _AuthManager()

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.current_user = current_user
        return await call_next(request)

    app.include_router(test_router)
    return app


def test_head_and_get_parity_on_valid_upload(tmp_path, monkeypatch):
    handler, alice_id, _bob_id, _upload_dir, alice_bytes = _make_upload_env(tmp_path, monkeypatch)
    app = _create_test_app(handler, current_user="alice")
    client = TestClient(app)

    # 1. GET response
    get_res = client.get(f"/api/upload/{alice_id}")
    assert get_res.status_code == 200
    assert get_res.content == alice_bytes
    assert int(get_res.headers["content-length"]) == len(alice_bytes)
    assert get_res.headers["x-content-type-options"] == "nosniff"

    # 2. HEAD response
    head_res = client.head(f"/api/upload/{alice_id}")
    assert head_res.status_code == 200
    assert len(head_res.content) == 0, "HEAD response must transfer 0 body bytes"
    assert head_res.headers["content-length"] == get_res.headers["content-length"]
    assert head_res.headers["content-type"] == get_res.headers["content-type"]
    assert head_res.headers["x-content-type-options"] == "nosniff"


def test_head_rejects_invalid_file_id(tmp_path, monkeypatch):
    handler, _alice_id, _bob_id, _upload_dir, _ = _make_upload_env(tmp_path, monkeypatch)
    app = _create_test_app(handler, current_user="alice")
    client = TestClient(app)

    get_res = client.get("/api/upload/invalid!id@bad")
    assert get_res.status_code == 400

    head_res = client.head("/api/upload/invalid!id@bad")
    assert head_res.status_code == 400
    assert len(head_res.content) == 0


def test_head_returns_404_on_missing_file(tmp_path, monkeypatch):
    handler, _alice_id, _bob_id, _upload_dir, _ = _make_upload_env(tmp_path, monkeypatch)
    app = _create_test_app(handler, current_user="alice")
    client = TestClient(app)

    missing_id = "c" * 32 + ".png"
    get_res = client.get(f"/api/upload/{missing_id}")
    assert get_res.status_code == 404

    head_res = client.head(f"/api/upload/{missing_id}")
    assert head_res.status_code == 404
    assert len(head_res.content) == 0


def test_head_denies_cross_owner_without_leaking_existence(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, _upload_dir, _ = _make_upload_env(tmp_path, monkeypatch)
    # Alice requests Bob's file
    app = _create_test_app(handler, current_user="alice")
    client = TestClient(app)

    get_res = client.get(f"/api/upload/{bob_id}")
    assert get_res.status_code == 404

    head_res = client.head(f"/api/upload/{bob_id}")
    assert head_res.status_code == 404
    assert len(head_res.content) == 0


def test_head_denies_unauthenticated_access(tmp_path, monkeypatch):
    handler, alice_id, _bob_id, _upload_dir, _ = _make_upload_env(tmp_path, monkeypatch)
    # Anonymous user (None) with auth configured
    app = _create_test_app(handler, current_user=None, auth_manager=_AuthManager())
    client = TestClient(app)

    get_res = client.get(f"/api/upload/{alice_id}")
    assert get_res.status_code == 403

    head_res = client.head(f"/api/upload/{alice_id}")
    assert head_res.status_code == 403
    assert len(head_res.content) == 0


def test_head_allows_admin_access(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, _upload_dir, bob_bytes = _make_upload_env(tmp_path, monkeypatch)
    # Admin requests Bob's file
    auth_mgr = _AuthManager(admins=["admin_user"])
    app = _create_test_app(handler, current_user="admin_user", auth_manager=auth_mgr)
    client = TestClient(app)

    get_res = client.get(f"/api/upload/{bob_id}")
    assert get_res.status_code == 200

    head_res = client.head(f"/api/upload/{bob_id}")
    assert head_res.status_code == 200
    assert len(head_res.content) == 0
    assert int(head_res.headers["content-length"]) == len(bob_bytes)


def test_head_probe_with_thumb_param(tmp_path, monkeypatch):
    handler, alice_id, _bob_id, _upload_dir, _alice_bytes = _make_upload_env(tmp_path, monkeypatch)
    app = _create_test_app(handler, current_user="alice")
    client = TestClient(app)

    # HEAD with thumb=1 on image
    head_res = client.head(f"/api/upload/{alice_id}?thumb=1")
    assert head_res.status_code == 200
    assert len(head_res.content) == 0


@pytest.mark.parametrize("cache", ["missing", "stale", "changed", "corrupt"])
def test_thumbnail_head_omits_unknown_representation_headers(tmp_path, monkeypatch, cache):
    """Unknown JPEG generation/fallback must not advertise original/stale metadata."""
    from PIL import Image
    from unittest.mock import patch
    handler, file_id, _, upload_dir, _ = _make_upload_env(tmp_path, monkeypatch)
    source = next(upload_dir.rglob(file_id))
    if cache != "corrupt":
        Image.new("RGB", (12, 15), "red").save(source)
    if cache in {"stale", "changed"}:
        thumb = upload_dir / ".thumbs" / (file_id + ".jpg")
        thumb.parent.mkdir()
        Image.new("RGB", (2, 2), "blue").save(thumb)
        os.utime(thumb, (1, 1))
        if cache == "changed":
            Image.new("RGB", (25, 30), "green").save(source)
    client = TestClient(_create_test_app(handler))
    with patch("PIL.Image.open", side_effect=AssertionError("HEAD must not decode")):
        response = client.head(f"/api/upload/{file_id}?thumb=1")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["x-content-type-options"] == "nosniff"
    for header in ("content-type", "content-length", "etag", "last-modified", "content-disposition"):
        assert header not in response.headers, (cache, header, response.headers)
    get = client.get(f"/api/upload/{file_id}?thumb=1")
    assert get.status_code == 200
    assert get.headers["content-type"] == ("image/png" if cache == "corrupt" else "image/jpeg")
    # Successful GET creates a current cache, and HEAD can now describe it exactly.
    head = client.head(f"/api/upload/{file_id}?thumb=1")
    if cache != "corrupt":
        for header in ("content-type", "content-length", "etag", "last-modified"):
            assert head.headers[header] == get.headers[header]
    else:
        assert "content-type" not in head.headers
        assert "content-length" not in head.headers


def test_thumbnail_head_current_cache_and_authorization(tmp_path, monkeypatch):
    from PIL import Image
    from unittest.mock import patch
    handler, file_id, _, upload_dir, _ = _make_upload_env(tmp_path, monkeypatch)
    source = next(upload_dir.rglob(file_id))
    Image.new("RGB", (12, 15), "red").save(source)
    client = TestClient(_create_test_app(handler))
    get = client.get(f"/api/upload/{file_id}?thumb=1")
    with patch("PIL.Image.open", side_effect=AssertionError("HEAD must not decode")):
        head = client.head(f"/api/upload/{file_id}?thumb=1")
    assert head.status_code == 200 and not head.content
    assert head.headers["content-type"] == "image/jpeg"
    assert head.headers["content-length"] == get.headers["content-length"]
    for user, status in (("bob", 404), (None, 403)):
        denied = TestClient(_create_test_app(handler, current_user=user)).head(f"/api/upload/{file_id}?thumb=1")
        assert denied.status_code == status
        assert "etag" not in denied.headers and "last-modified" not in denied.headers
