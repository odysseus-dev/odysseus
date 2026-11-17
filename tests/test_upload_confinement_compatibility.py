"""Regression suite for upload confinement compatibility on symlinked and ordinary roots.

Verifies:
1. Ordinary upload root.
2. Upload root that is a symlink to an authorized directory.
3. Valid file in nested upload directory.
4. Symlinked in-root directory resolving safely inside the root.
5. Symlink escaping outside the authorized root — rejected.
6. Malformed upload ID — rejected.
7. Missing upload — rejected.
8. Foreign-user upload — rejected.
9. Valid GET/HEAD parity on both ordinary and symlinked roots.
10. Cached, missing and stale thumbnail HEAD behavior (zero body, no generation).
11. No directory traversal or unauthorized file disclosure.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient

from src.path_confinement import canonical_root, confine, PathEscape
from src.upload_handler import UploadHandler, secure_filename
import routes.upload_routes as upload_routes_mod
import src.constants as constants


class _AuthManager:
    is_configured = True

    def __init__(self, admins=("admin",)):
        self._admins = set(admins)

    def is_admin(self, user: str | None) -> bool:
        return user in self._admins


def _build_test_app(handler: UploadHandler, default_user: str | None = "alice") -> FastAPI:
    test_router = APIRouter(prefix="/api/upload", tags=["uploads"])
    orig_router = upload_routes_mod.router
    upload_routes_mod.router = test_router
    try:
        upload_routes_mod.setup_upload_routes(handler)
    finally:
        upload_routes_mod.router = orig_router

    app = FastAPI()
    app.state.auth_manager = _AuthManager()

    @app.middleware("http")
    async def inject_user(request, call_next):
        header_user = request.headers.get("x-test-user")
        if header_user is not None:
            request.state.current_user = header_user if header_user != "none" else None
        else:
            request.state.current_user = default_user
        return await call_next(request)

    app.include_router(test_router)
    return app


def _create_fixture_env(tmp_path: Path, monkeypatch, *, symlinked_root: bool = False):
    if symlinked_root:
        real_uploads = tmp_path / "real_uploads"
        real_uploads.mkdir(parents=True, exist_ok=True)
        upload_dir = tmp_path / "symlink_uploads"
        os.symlink(real_uploads, upload_dir)
        storage_base = real_uploads
    else:
        upload_dir = tmp_path / "ordinary_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_base = upload_dir

    dated_dir = storage_base / "2026" / "10" / "29"
    dated_dir.mkdir(parents=True, exist_ok=True)

    alice_id = "a" * 32 + ".png"
    bob_id = "b" * 32 + ".png"
    nested_id = "c" * 32 + ".png"

    # Valid PNG image bytes using PIL
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    alice_path = storage_base / alice_id
    bob_path = storage_base / bob_id
    nested_path = dated_dir / nested_id

    alice_path.write_bytes(png_bytes)
    bob_path.write_bytes(png_bytes)
    nested_path.write_bytes(png_bytes)

    index = {
        f"alice:{alice_id}": {
            "id": alice_id,
            "path": str(alice_path),
            "mime": "image/png",
            "size": len(png_bytes),
            "name": "alice.png",
            "original_name": "alice.png",
            "owner": "alice",
        },
        f"bob:{bob_id}": {
            "id": bob_id,
            "path": str(bob_path),
            "mime": "image/png",
            "size": len(png_bytes),
            "name": "bob.png",
            "original_name": "bob.png",
            "owner": "bob",
        },
        f"alice:{nested_id}": {
            "id": nested_id,
            "path": str(nested_path),
            "mime": "image/png",
            "size": len(png_bytes),
            "name": "nested.png",
            "original_name": "nested.png",
            "owner": "alice",
        },
    }
    (storage_base / "uploads.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(upload_dir))

    handler = UploadHandler(str(tmp_path), str(upload_dir))
    app = _build_test_app(handler)
    client = TestClient(app)

    return SimpleNamespace(
        upload_dir=upload_dir,
        storage_base=storage_base,
        alice_id=alice_id,
        bob_id=bob_id,
        nested_id=nested_id,
        png_bytes=png_bytes,
        handler=handler,
        app=app,
        client=client,
    )


# 1. Ordinary upload root
def test_matrix_01_ordinary_upload_root(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=False)
    get_res = env.client.get(f"/api/upload/{env.alice_id}")
    assert get_res.status_code == 200
    assert get_res.content == env.png_bytes

    head_res = env.client.head(f"/api/upload/{env.alice_id}")
    assert head_res.status_code == 200
    assert len(head_res.content) == 0


# 2. Upload root that is a symlink to an authorized directory
def test_matrix_02_symlinked_upload_root(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=True)
    get_res = env.client.get(f"/api/upload/{env.alice_id}")
    assert get_res.status_code == 200
    assert get_res.content == env.png_bytes

    head_res = env.client.head(f"/api/upload/{env.alice_id}")
    assert head_res.status_code == 200
    assert len(head_res.content) == 0


# 3. Valid file in nested upload directory
def test_matrix_03_nested_upload_directory(tmp_path: Path, monkeypatch):
    for symlinked in (False, True):
        env = _create_fixture_env(tmp_path / f"nested_{symlinked}", monkeypatch, symlinked_root=symlinked)
        get_res = env.client.get(f"/api/upload/{env.nested_id}")
        assert get_res.status_code == 200
        assert get_res.content == env.png_bytes

        head_res = env.client.head(f"/api/upload/{env.nested_id}")
        assert head_res.status_code == 200
        assert len(head_res.content) == 0


# 4. Symlinked in-root directory resolving safely inside the root
def test_matrix_04_symlinked_in_root_directory_safe(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=False)
    real_sub = env.storage_base / "real_sub"
    real_sub.mkdir(parents=True, exist_ok=True)
    sub_file_id = "d" * 32 + ".png"
    (real_sub / sub_file_id).write_bytes(env.png_bytes)

    link_sub = env.storage_base / "link_sub"
    os.symlink(real_sub, link_sub)

    # Register in uploads.json pointing via symlinked in-root directory
    uploads_json = env.storage_base / "uploads.json"
    index = json.loads(uploads_json.read_text(encoding="utf-8"))
    index[f"alice:{sub_file_id}"] = {
        "id": sub_file_id,
        "path": str(link_sub / sub_file_id),
        "mime": "image/png",
        "size": len(env.png_bytes),
        "name": "sub.png",
        "owner": "alice",
    }
    uploads_json.write_text(json.dumps(index), encoding="utf-8")

    get_res = env.client.get(f"/api/upload/{sub_file_id}")
    assert get_res.status_code == 200
    assert get_res.content == env.png_bytes

    head_res = env.client.head(f"/api/upload/{sub_file_id}")
    assert head_res.status_code == 200
    assert len(head_res.content) == 0


# 5. Symlink escaping outside the authorized root — rejected
def test_matrix_05_symlink_escaping_outside_root_rejected(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=False)

    outside_dir = tmp_path / "outside_store"
    outside_dir.mkdir(parents=True, exist_ok=True)
    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("CONFIDENTIAL_SYSTEM_DATA", encoding="utf-8")

    # A: Direct file symlink escaping root
    escape_file_id = "e" * 32 + ".txt"
    os.symlink(secret_file, env.storage_base / escape_file_id)

    uploads_json = env.storage_base / "uploads.json"
    index = json.loads(uploads_json.read_text(encoding="utf-8"))
    index[f"alice:{escape_file_id}"] = {
        "id": escape_file_id,
        "path": str(env.storage_base / escape_file_id),
        "mime": "text/plain",
        "size": len("CONFIDENTIAL_SYSTEM_DATA"),
        "name": "escape.txt",
        "owner": "alice",
    }
    uploads_json.write_text(json.dumps(index), encoding="utf-8")

    get_res = env.client.get(f"/api/upload/{escape_file_id}")
    assert get_res.status_code == 403
    head_res = env.client.head(f"/api/upload/{escape_file_id}")
    assert head_res.status_code == 403
    assert len(head_res.content) == 0

    # B: Directory symlink escaping root
    escape_dir_link = env.storage_base / "evil_sub"
    os.symlink(outside_dir, escape_dir_link)
    escape_dir_id = "f" * 32 + ".txt"
    index[f"alice:{escape_dir_id}"] = {
        "id": escape_dir_id,
        "path": str(escape_dir_link / "secret.txt"),
        "mime": "text/plain",
        "size": len("CONFIDENTIAL_SYSTEM_DATA"),
        "name": "escape_sub.txt",
        "owner": "alice",
    }
    uploads_json.write_text(json.dumps(index), encoding="utf-8")

    get_dir = env.client.get(f"/api/upload/{escape_dir_id}")
    assert get_dir.status_code == 403
    head_dir = env.client.head(f"/api/upload/{escape_dir_id}")
    assert head_dir.status_code == 403
    assert len(head_dir.content) == 0


# 6. Malformed upload ID — rejected
def test_matrix_06_malformed_upload_id_rejected(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=True)

    malformed_ids = [
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "invalid!id@specials",
        "short_id.png",
        "g" * 32 + ".png",  # non-hex character 'g'
        " " + "a" * 32 + ".png",
        "a" * 31,
    ]
    for bad_id in malformed_ids:
        get_res = env.client.get(f"/api/upload/{bad_id}")
        assert get_res.status_code in (400, 403, 404), f"GET failed for {bad_id}: {get_res.status_code}"
        head_res = env.client.head(f"/api/upload/{bad_id}")
        assert head_res.status_code == get_res.status_code, f"HEAD failed for {bad_id}: {head_res.status_code}"
        assert len(head_res.content) == 0


# 7. Missing upload — rejected
def test_matrix_07_missing_upload_rejected(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=True)
    missing_id = "1" * 32 + ".png"

    get_res = env.client.get(f"/api/upload/{missing_id}")
    assert get_res.status_code == 404
    head_res = env.client.head(f"/api/upload/{missing_id}")
    assert head_res.status_code == 404
    assert len(head_res.content) == 0


# 8. Foreign-user upload — rejected
def test_matrix_08_foreign_user_upload_rejected(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=True)

    # Bob attempts to access Alice's upload
    bob_get = env.client.get(f"/api/upload/{env.alice_id}", headers={"x-test-user": "bob"})
    assert bob_get.status_code == 404
    bob_head = env.client.head(f"/api/upload/{env.alice_id}", headers={"x-test-user": "bob"})
    assert bob_head.status_code == 404
    assert len(bob_head.content) == 0

    # Unauthenticated attempts
    unauth_get = env.client.get(f"/api/upload/{env.alice_id}", headers={"x-test-user": "none"})
    assert unauth_get.status_code in (401, 403)
    unauth_head = env.client.head(f"/api/upload/{env.alice_id}", headers={"x-test-user": "none"})
    assert unauth_head.status_code == unauth_get.status_code
    assert len(unauth_head.content) == 0

    # Admin access succeeds
    admin_get = env.client.get(f"/api/upload/{env.alice_id}", headers={"x-test-user": "admin"})
    assert admin_get.status_code == 200
    admin_head = env.client.head(f"/api/upload/{env.alice_id}", headers={"x-test-user": "admin"})
    assert admin_head.status_code == 200
    assert len(admin_head.content) == 0


# 9. Valid GET/HEAD parity on both ordinary and symlinked roots
def test_matrix_09_get_head_parity(tmp_path: Path, monkeypatch):
    for symlinked in (False, True):
        env = _create_fixture_env(tmp_path / f"parity_{symlinked}", monkeypatch, symlinked_root=symlinked)
        get_res = env.client.get(f"/api/upload/{env.alice_id}")
        head_res = env.client.head(f"/api/upload/{env.alice_id}")

        assert get_res.status_code == 200
        assert head_res.status_code == 200
        assert len(head_res.content) == 0
        assert get_res.headers.get("x-content-type-options") == "nosniff"
        assert head_res.headers.get("x-content-type-options") == "nosniff"
        assert get_res.headers.get("content-type") == head_res.headers.get("content-type")
        assert get_res.headers.get("content-length") == head_res.headers.get("content-length")


# 10. Cached, missing and stale thumbnail HEAD behavior
def test_matrix_10_thumbnail_head_behavior(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=True)
    thumb_path = env.storage_base / ".thumbs" / f"{env.alice_id}.jpg"

    from unittest.mock import patch

    # Case A: Missing thumbnail
    assert not thumb_path.exists()
    with patch("PIL.Image.open", side_effect=AssertionError("HEAD must not decode")):
        head_missing = env.client.head(f"/api/upload/{env.alice_id}?thumb=1")
    assert head_missing.status_code == 200
    assert len(head_missing.content) == 0
    # Must NOT generate thumbnail on disk during HEAD
    assert not thumb_path.exists()
    # Must NOT advertise Content-Length when thumbnail is ungenerated
    assert "content-length" not in head_missing.headers

    # Generate thumbnail via GET
    get_gen = env.client.get(f"/api/upload/{env.alice_id}?thumb=1")
    assert get_gen.status_code == 200
    assert thumb_path.exists()
    thumb_mtime = os.path.getmtime(thumb_path)

    # Case B: Cached (fresh) thumbnail
    with patch("PIL.Image.open", side_effect=AssertionError("HEAD must not decode")):
        head_cached = env.client.head(f"/api/upload/{env.alice_id}?thumb=1")
    assert head_cached.status_code == 200
    assert len(head_cached.content) == 0
    assert head_cached.headers.get("content-type") == "image/jpeg"
    assert "content-length" in head_cached.headers
    assert head_cached.headers["content-length"] == str(len(thumb_path.read_bytes()))
    # Mtime did not change
    assert os.path.getmtime(thumb_path) == thumb_mtime

    # Case C: Stale thumbnail (original modified after thumb)
    alice_file = env.storage_base / env.alice_id
    future_time = time.time() + 100
    os.utime(alice_file, (future_time, future_time))

    with patch("PIL.Image.open", side_effect=AssertionError("HEAD must not decode")):
        head_stale = env.client.head(f"/api/upload/{env.alice_id}?thumb=1")
    assert head_stale.status_code == 200
    assert len(head_stale.content) == 0
    # Must NOT advertise old stale Content-Length
    assert "content-length" not in head_stale.headers
    # Must NOT regenerate thumb during HEAD
    assert os.path.getmtime(thumb_path) == thumb_mtime


# 11. No directory traversal or unauthorized file disclosure
def test_matrix_11_directory_traversal_prevention(tmp_path: Path, monkeypatch):
    env = _create_fixture_env(tmp_path, monkeypatch, symlinked_root=True)

    secret = tmp_path / "system_secret.txt"
    secret.write_text("SENSITIVE_KEY_VALUE", encoding="utf-8")

    traversal_paths = [
        f"/api/upload/..%2f..%2f{secret.name}",
        f"/api/upload/%2e%2e%2f%2e%2e%2f{secret.name}",
        f"/api/upload/../../{secret.name}",
        f"/api/upload/{env.alice_id}/../../{secret.name}",
    ]
    for target in traversal_paths:
        res = env.client.get(target)
        assert res.status_code in (400, 403, 404)
        assert "SENSITIVE_KEY_VALUE" not in res.text
