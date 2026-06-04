import pytest
import httpx

from src import ai_interaction
import src.database as database
import src.settings as settings


class _ImageResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"data": [{"url": "https://cdn.example/generated.png"}]}


class _DownloadResponse:
    status_code = 200
    content = b"png-bytes"


@pytest.mark.asyncio
async def test_dalle_gallery_metadata_records_requested_quality(monkeypatch, tmp_path):
    captured = {}
    saved = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["payload"] = dict(json)
            return _ImageResponse()

    class FakeGalleryImage:
        def __init__(self, **kwargs):
            saved.update(kwargs)

    class FakeDb:
        def add(self, image):
            captured["added"] = image

        def commit(self):
            captured["committed"] = True

        def close(self):
            captured["closed"] = True

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(ai_interaction, "_resolve_model", lambda spec, owner=None: (
        "https://api.example/v1/chat/completions",
        "dall-e-3",
        {},
    ))
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(httpx, "get", lambda url, timeout: _DownloadResponse())
    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(database, "GalleryImage", FakeGalleryImage, raising=False)

    result = await ai_interaction.do_generate_image(
        "a bright robot\ndall-e-3\n1024x1024\nhigh",
        session_id="session-1",
        owner="alice",
    )

    assert "quality" not in captured["payload"]
    assert saved["quality"] == "high"
    assert result["image_quality"] == "high"
    assert saved["model"] == "dall-e-3"
    assert saved["session_id"] == "session-1"
    assert saved["owner"] == "alice"
