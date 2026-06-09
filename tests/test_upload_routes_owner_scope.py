import asyncio
import builtins
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


class _Request:
    def __init__(self, user=None, auth_manager=None, body=None):
        self.state = SimpleNamespace(current_user=user)
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
        self.client = SimpleNamespace(host="127.0.0.1")
        self._body = body

    async def json(self):
        return self._body


def _upload_endpoints(upload_handler, monkeypatch):
    import fastapi.dependencies.utils as dependency_utils
    from routes.upload_routes import router, setup_upload_routes

    monkeypatch.setattr(dependency_utils, "ensure_multipart_is_installed", lambda: None)
    before = len(router.routes)
    setup_upload_routes(upload_handler)
    routes = router.routes[before:]
    return {route.endpoint.__name__: route.endpoint for route in routes}


def _make_upload_store(tmp_path, monkeypatch):
    from src.upload_handler import UploadHandler
    from src import constants

    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "06" / "02"
    dated.mkdir(parents=True)

    alice_id = "a" * 32 + ".png"
    bob_id = "b" * 32 + ".png"
    alice_path = dated / alice_id
    bob_path = dated / bob_id
    alice_path.write_bytes(b"alice image bytes")
    bob_path.write_bytes(b"bob image bytes")

    index = {
        "alice:h1": {
            "id": alice_id,
            "path": str(alice_path),
            "mime": "image/png",
            "size": alice_path.stat().st_size,
            "name": "alice.png",
            "original_name": "alice.png",
            "owner": "alice",
        },
        "bob:h2": {
            "id": bob_id,
            "path": str(bob_path),
            "mime": "image/png",
            "size": bob_path.stat().st_size,
            "name": "bob.png",
            "original_name": "bob.png",
            "owner": "bob",
        },
    }
    (upload_dir / "uploads.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(upload_dir))
    return UploadHandler(str(tmp_path), str(upload_dir)), alice_id, bob_id, upload_dir


def _guard_cache_open(monkeypatch, cache_path, blocked_modes):
    original_open = builtins.open

    def guarded_open(path, mode="r", *args, **kwargs):
        if str(path) == str(cache_path) and any(flag in mode for flag in blocked_modes):
            raise AssertionError(f"owner gate should run before opening {cache_path}")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)


def _add_upload_row(upload_dir, row):
    db_path = upload_dir / "uploads.json"
    index = json.loads(db_path.read_text(encoding="utf-8"))
    index[f"{row.get('owner')}:{row['id']}"] = row
    db_path.write_text(json.dumps(index), encoding="utf-8")


def _add_upload_symlink(upload_dir, file_id, target_path, owner="alice"):
    dated = upload_dir / "2026" / "06" / "02"
    link_path = dated / file_id
    try:
        os.symlink(target_path, link_path)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    _add_upload_row(
        upload_dir,
        {
            "id": file_id,
            "path": str(link_path),
            "mime": "image/png",
            "size": target_path.stat().st_size,
            "name": "escape.png",
            "original_name": "escape.png",
            "owner": owner,
        },
    )
    return link_path


def test_download_file_denies_anonymous_when_auth_is_configured(tmp_path, monkeypatch):
    handler, alice_id, _bob_id, _upload_dir = _make_upload_store(tmp_path, monkeypatch)
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(download_file(_Request(auth_manager=_AuthManager()), alice_id))

    assert exc.value.status_code == 403


def test_download_file_denies_cross_owner_without_leaking_file(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, _upload_dir = _make_upload_store(tmp_path, monkeypatch)
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(download_file(_Request(user="alice", auth_manager=_AuthManager()), bob_id))

    assert exc.value.status_code == 404


def test_download_file_allows_same_owner(tmp_path, monkeypatch):
    handler, alice_id, _bob_id, _upload_dir = _make_upload_store(tmp_path, monkeypatch)
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]

    response = asyncio.run(
        download_file(_Request(user="alice", auth_manager=_AuthManager()), alice_id)
    )

    assert response.path.endswith(alice_id)
    assert response.media_type == "image/png"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_download_file_allows_admin_to_read_other_owner_upload(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, _upload_dir = _make_upload_store(tmp_path, monkeypatch)
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]

    response = asyncio.run(
        download_file(
            _Request(user="admin", auth_manager=_AuthManager(admins={"admin"})),
            bob_id,
        )
    )

    assert response.path.endswith(bob_id)
    assert response.media_type == "image/png"


def test_download_file_rejects_upload_symlink_escape(tmp_path, monkeypatch):
    handler, _alice_id, _bob_id, upload_dir = _make_upload_store(tmp_path, monkeypatch)
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]
    escape_id = "c" * 32 + ".png"
    outside = tmp_path / "outside-upload-root.png"
    outside.write_bytes(b"outside upload root")
    _add_upload_symlink(upload_dir, escape_id, outside)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            download_file(
                _Request(user="alice", auth_manager=_AuthManager()),
                escape_id,
            )
        )

    assert exc.value.status_code == 403


def test_download_file_keeps_owner_gate_before_path_resolution(tmp_path, monkeypatch):
    handler, _alice_id, _bob_id, upload_dir = _make_upload_store(tmp_path, monkeypatch)
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]
    bob_escape_id = "d" * 32 + ".png"
    outside = tmp_path / "bob-outside-upload-root.png"
    outside.write_bytes(b"bob outside upload root")
    _add_upload_symlink(upload_dir, bob_escape_id, outside, owner="bob")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            download_file(
                _Request(user="alice", auth_manager=_AuthManager()),
                bob_escape_id,
            )
        )

    assert exc.value.status_code == 404


def test_get_vision_text_denies_cross_owner_before_cache_read(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, upload_dir = _make_upload_store(tmp_path, monkeypatch)
    get_vision_text = _upload_endpoints(handler, monkeypatch)["get_vision_text"]
    cache_dir = upload_dir / ".vision"
    cache_dir.mkdir()
    cache_path = cache_dir / f"{bob_id}.txt"
    cache_path.write_text("bob private cached text", encoding="utf-8")
    _guard_cache_open(monkeypatch, cache_path, blocked_modes=("r",))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_vision_text(
                _Request(user="alice", auth_manager=_AuthManager()),
                bob_id,
            )
        )

    assert exc.value.status_code == 404


def test_get_vision_text_denies_cross_owner_before_image_analysis(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, _upload_dir = _make_upload_store(tmp_path, monkeypatch)
    get_vision_text = _upload_endpoints(handler, monkeypatch)["get_vision_text"]

    def fail_analysis(_path):
        raise AssertionError("owner gate should run before image analysis")

    monkeypatch.setattr("src.document_processor.analyze_image_with_vl", fail_analysis)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_vision_text(
                _Request(user="alice", auth_manager=_AuthManager()),
                bob_id,
                force=1,
            )
        )

    assert exc.value.status_code == 404


def test_get_vision_text_rejects_upload_symlink_escape_before_analysis(tmp_path, monkeypatch):
    handler, _alice_id, _bob_id, upload_dir = _make_upload_store(tmp_path, monkeypatch)
    get_vision_text = _upload_endpoints(handler, monkeypatch)["get_vision_text"]
    escape_id = "e" * 32 + ".png"
    outside = tmp_path / "vision-outside-upload-root.png"
    outside.write_bytes(b"outside upload root")
    _add_upload_symlink(upload_dir, escape_id, outside)

    def fail_analysis(_path):
        raise AssertionError("upload root gate should run before image analysis")

    monkeypatch.setattr("src.document_processor.analyze_image_with_vl", fail_analysis)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_vision_text(
                _Request(user="alice", auth_manager=_AuthManager()),
                escape_id,
                force=1,
            )
        )

    assert exc.value.status_code == 403


def test_put_vision_text_denies_cross_owner_before_cache_write(tmp_path, monkeypatch):
    handler, _alice_id, bob_id, upload_dir = _make_upload_store(tmp_path, monkeypatch)
    put_vision_text = _upload_endpoints(handler, monkeypatch)["put_vision_text"]
    cache_path = upload_dir / ".vision" / f"{bob_id}.txt"
    _guard_cache_open(monkeypatch, cache_path, blocked_modes=("w", "a", "+"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            put_vision_text(
                _Request(
                    user="alice",
                    auth_manager=_AuthManager(),
                    body={"text": "edited text"},
                ),
                bob_id,
            )
        )

    assert exc.value.status_code == 404
    assert not cache_path.exists()


def test_put_vision_text_allows_same_owner_to_write_cache(tmp_path, monkeypatch):
    handler, alice_id, _bob_id, upload_dir = _make_upload_store(tmp_path, monkeypatch)
    put_vision_text = _upload_endpoints(handler, monkeypatch)["put_vision_text"]

    response = asyncio.run(
        put_vision_text(
            _Request(
                user="alice",
                auth_manager=_AuthManager(),
                body={"text": "edited alice text"},
            ),
            alice_id,
        )
    )

    assert response == {"ok": True}
    assert (upload_dir / ".vision" / f"{alice_id}.txt").read_text(
        encoding="utf-8"
    ) == "edited alice text"
