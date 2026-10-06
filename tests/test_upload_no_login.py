import json
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
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
    app.state.auth_manager = SimpleNamespace(is_configured=True)
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
