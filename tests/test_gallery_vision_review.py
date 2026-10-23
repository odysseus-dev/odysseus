"""Regression coverage for the remaining #6019 review asks.

- Each supported provider shape gets the photo in its own request format
  through the shared transport (mocked HTTP / Codex stream, real llm_core).
- Unsupported files and videos are rejected before anything is encoded or
  sent, at the helper and at both routes.
- Routes answer with fixed messages, never exception text.
- Endpoint discovery runs off the event loop.
- The OCR picker skips non-chat endpoints, the settings path copy is
  current, and a single description refreshes the count and grid.
"""
import asyncio
import base64
import json
import re
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage
import routes.gallery.gallery_routes as gallery_routes
import src.llm_core as llm_core

_REPO = Path(__file__).resolve().parents[1]
_GALLERY_JS = (_REPO / "static" / "js" / "gallery.js").read_text(encoding="utf-8")
_PNG = b"\x89PNG\r\n\x1a\nfake"


class _HttpResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.is_success = True
        self.text = json.dumps(data)

    def json(self):
        return self._data


@pytest.fixture
def transport(monkeypatch):
    sent = []
    replies = {}

    async def fake_post(client, url, headers, json=None, timeout=None):
        sent.append({"url": url, "payload": json})
        return _HttpResp(replies["http"])

    async def fake_stream(url, model, messages, **kwargs):
        sent.append({"url": url, "messages": messages})
        yield 'data: {"delta": "a receipt from a corner cafe"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)
    return sent, replies


def _use_candidate(monkeypatch, url, model):
    async def _cands(override, owner, session_id=None):
        return [(url, model, {})]
    monkeypatch.setattr(gallery_routes, "_resolve_vision_candidates", _cands)


@pytest.mark.parametrize("url,model,provider,reply", [
    ("http://localhost:8080/v1", "qwen2.5-vl", "openai",
     {"choices": [{"message": {"content": "a receipt from a corner cafe"}}]}),
    ("http://localhost:11434", "gemma3:27b", "ollama",
     {"message": {"role": "assistant", "content": "a receipt from a corner cafe"}, "done": True}),
    ("https://api.anthropic.com/v1", "claude-sonnet-4-5", "anthropic",
     {"content": [{"type": "text", "text": "a receipt from a corner cafe"}]}),
    ("https://chatgpt.com/backend-api/codex", "gpt-5", "chatgpt-subscription", None),
])
def test_each_provider_shape_gets_the_image(tmp_path, monkeypatch, transport, url, model, provider, reply):
    sent, replies = transport
    replies["http"] = reply
    assert llm_core._detect_provider(url) == provider
    img = tmp_path / "photo.png"
    img.write_bytes(_PNG)
    _use_candidate(monkeypatch, url, model)

    text, answered = asyncio.run(gallery_routes._call_vision_model(img, "Describe.", "alice"))
    assert text == "a receipt from a corner cafe" and answered == model

    b64 = base64.b64encode(_PNG).decode()
    request = sent[-1]
    if provider == "ollama":
        assert request["payload"]["messages"][0]["images"] == [b64]
    elif provider == "anthropic":
        blocks = request["payload"]["messages"][0]["content"]
        assert {"type": "base64", "media_type": "image/png", "data": b64} in [b.get("source") for b in blocks]
    else:
        content = (request["payload"]["messages"] if "payload" in request else request["messages"])[0]["content"]
        assert {"url": f"data:image/png;base64,{b64}"} in [b.get("image_url") for b in content]


@pytest.mark.parametrize("name", ["clip.mp4", "scan.bmp", "photo.heic", "noextension"])
def test_unsupported_files_are_rejected_before_anything_is_sent(tmp_path, monkeypatch, transport, name):
    sent, _ = transport
    resolved = []

    async def _cands(*a, **k):
        resolved.append(a)
        return [("http://localhost:8080/v1", "qwen2.5-vl", {})]

    monkeypatch.setattr(gallery_routes, "_resolve_vision_candidates", _cands)
    f = tmp_path / name
    f.write_bytes(b"not an image")
    with pytest.raises(gallery_routes.VisionUnsupportedFileError):
        asyncio.run(gallery_routes._call_vision_model(f, "Describe.", "alice"))
    assert resolved == [] and sent == []


def test_every_vision_error_maps_to_its_own_fixed_message():
    for kind in gallery_routes._VISION_ERROR_TYPES:
        assert gallery_routes._vision_error_message(kind()) == kind.message
        assert str(kind()) == kind.message
    assert gallery_routes._vision_error_message(ValueError("internal detail")) == "Vision model request failed"


def test_discovery_runs_off_the_event_loop(monkeypatch):
    import src.document_processor as dp
    import src.endpoint_resolver as er
    import src.chat_helpers as ch

    def slow_resolve(configured, owner=None, session_id=None):
        time.sleep(0.3)  # a blocking model-list probe
        return ("http://localhost:8080/v1", "qwen2.5-vl", {})

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "qwen2.5-vl"})
    monkeypatch.setattr(dp, "_resolve_vl_model", slow_resolve)
    monkeypatch.setattr(er, "resolve_vision_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(ch, "model_supports_vision", lambda *a, **k: True)

    async def main():
        ticks = 0
        stop = False

        async def ticker():
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ticker())
        result = await gallery_routes._resolve_vision_candidates("", "alice")
        stop = True
        await task
        return result, ticks

    result, ticks = asyncio.run(main())
    assert result == [("http://localhost:8080/v1", "qwen2.5-vl", {})]
    assert ticks >= 10, "the event loop was blocked while models were discovered"


@pytest.fixture
def gallery_client(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'g.db'}",
                           connect_args={"check_same_thread": False}, poolclass=NullPool)
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(gallery_routes, "SessionLocal", factory)
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(gallery_routes, "_gallery_image_path", lambda name: tmp_path / name)
    db = factory()
    for image_id, filename in (("photo", f"{uuid.uuid4().hex}.png"), ("video", f"{uuid.uuid4().hex}.mp4")):
        (tmp_path / filename).write_bytes(_PNG)
        db.add(GalleryImage(id=image_id, filename=filename, prompt="", model="", tags="", ai_tags="",
                            caption="", owner="alice", is_active=True, file_size=12))
    db.commit()
    db.close()
    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    return TestClient(app)


@pytest.mark.parametrize("route", ["/api/gallery/video/ocr", "/api/gallery/video/ai-tag"])
def test_routes_refuse_videos_without_calling_a_model(gallery_client, monkeypatch, route):
    calls = []

    async def _never(*a, **k):
        calls.append(a)
        return "x", "m"

    monkeypatch.setattr(gallery_routes, "_call_vision_model", _never)
    data = gallery_client.post(route).json()
    assert "video" in data["error"] and calls == []


@pytest.mark.parametrize("route", ["/api/gallery/photo/ocr", "/api/gallery/photo/ai-tag"])
def test_routes_answer_with_fixed_messages_not_exception_text(gallery_client, monkeypatch, route):
    async def _not_capable(*a, **k):
        raise gallery_routes.VisionModelNotCapableError()

    monkeypatch.setattr(gallery_routes, "_call_vision_model", _not_capable)
    assert gallery_client.post(route).json() == {"error": gallery_routes.VisionModelNotCapableError.message}

    async def _internal(*a, **k):
        raise ValueError("Traceback: secret internal path /srv/app")

    monkeypatch.setattr(gallery_routes, "_call_vision_model", _internal)
    data = gallery_client.post(route).json()
    assert "secret" not in json.dumps(data) and data["error"] in ("OCR description failed", "Auto-tagging failed")


def test_ocr_picker_skips_non_chat_endpoints():
    picker = _GALLERY_JS.split("getElementById('gallery-ocr-model')", 2)[2].split("sel.appendChild(opt)", 1)[0]
    assert "(item.model_type || 'llm') !== 'llm'" in picker


def test_ocr_selector_points_to_the_current_settings_path():
    assert "Use Settings → AI Defaults → Vision" in _GALLERY_JS
    assert not re.search(r"Use Settings → Vision\b", _GALLERY_JS)


def test_single_description_refreshes_the_count_and_grid():
    ok_branch = _GALLERY_JS.split("/api/gallery/${img.id}/ocr", 1)[1].split("} else {", 1)[0]
    assert "if (!data.skipped && !redo)" in ok_branch
    assert "_totalDescribed++" in ok_branch and "_updateOcrCount()" in ok_branch
    assert ok_branch.index("img.caption = data.caption") < ok_branch.index("_renderGrid()")
