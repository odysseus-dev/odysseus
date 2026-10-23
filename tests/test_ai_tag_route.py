"""Route-level coverage for /api/gallery/{id}/ai-tag (PR #5965 review).

Drives the real handler with a mocked vision provider: the output-budget key
and value per provider, and that empty, reasoning-only, structured and
wrapped responses either yield the visible tags or fail without saving.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage
import routes.gallery.gallery_routes as gallery_routes
import src.document_processor as document_processor
from src.constants import VISION_MAX_TOKENS


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = str(data)

    def json(self):
        return self._data


@pytest.fixture
def tag_route(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'gallery.db'}",
                           connect_args={"check_same_thread": False}, poolclass=NullPool)
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(gallery_routes, "SessionLocal", factory)

    filename = f"{uuid.uuid4().hex}.png"
    (tmp_path / filename).write_bytes(b"png-bytes")
    monkeypatch.setattr(gallery_routes, "_gallery_image_path", lambda name: tmp_path / name)
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "alice")
    db = factory()
    db.add(GalleryImage(id="img1", filename=filename, prompt="", model="", tags="", ai_tags="",
                        owner="alice", is_active=True, file_size=9))
    db.commit()
    db.close()

    state = {"url": "http://localhost:11434/v1/chat/completions", "model": "gemma3:27b", "reply": None, "sent": []}
    monkeypatch.setattr(document_processor, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "x"})
    monkeypatch.setattr(document_processor, "_resolve_vl_model",
                        lambda configured, owner=None: (state["url"], state["model"], {}))

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            state["sent"].append(json)
            return _Resp(state["reply"])

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    client = TestClient(app)

    def run(reply, url=None, model=None):
        state["reply"] = reply
        if url:
            state["url"] = url
        if model:
            state["model"] = model
        result = client.post("/api/gallery/img1/ai-tag").json()
        db = factory()
        try:
            saved = db.query(GalleryImage).filter(GalleryImage.id == "img1").first().ai_tags
        finally:
            db.close()
        return result, saved, (state["sent"][-1] if state["sent"] else None)

    return run


def _openai(message):
    return {"choices": [{"message": message}]}


@pytest.mark.parametrize("url,model,key", [
    ("http://localhost:11434/v1/chat/completions", "gemma3:27b", "max_tokens"),
    ("https://api.openai.com/v1/chat/completions", "gpt-5-mini", "max_completion_tokens"),
    ("https://api.anthropic.com/v1/messages", "claude-sonnet-4-5", "max_tokens"),
])
def test_each_provider_gets_the_vision_output_budget(tag_route, url, model, key):
    reply = ({"content": [{"type": "text", "text": "cat, sofa"}]} if "anthropic" in url
             else _openai({"content": "cat, sofa"}))
    result, saved, sent = tag_route(reply, url=url, model=model)
    assert sent[key] == VISION_MAX_TOKENS
    assert {"max_tokens", "max_completion_tokens"} & set(sent) == {key}
    assert result == {"ok": True, "ai_tags": "cat, sofa"} and saved == "cat, sofa"


@pytest.mark.parametrize("message", [
    {"content": ""},
    {"content": None},
    {"content": "<think>looking at the photo carefully</think>"},
])
def test_no_visible_answer_is_an_error_and_saves_nothing(tag_route, message):
    result, saved, _ = tag_route(_openai(message))
    assert "error" in result and "ok" not in result
    assert saved == ""


def test_reasoning_content_answer_is_used(tag_route):
    result, saved, _ = tag_route(_openai({"content": "", "reasoning_content": "bike, street"}))
    assert result["ok"] and saved == "bike, street"


def test_structured_reply_tags_come_from_the_text_part_only(tag_route):
    result, saved, _ = tag_route(_openai({"content": [
        {"type": "thinking", "thinking": [{"type": "text", "text": "pier or dock, hard to tell"}]},
        {"type": "text", "text": "lake, boat, dusk"},
    ]}))
    assert result["ok"] and saved == "lake, boat, dusk"
    assert "pier" not in saved


def test_wrapped_thinking_is_stripped_before_saving(tag_route):
    result, saved, _ = tag_route(_openai({"content": "<think>the user wants tags</think>dog, park, sunny"}))
    assert result["ok"] and saved == "dog, park, sunny"


def test_failed_provider_request_saves_nothing(tag_route, monkeypatch):
    import httpx
    sent = []

    class _Down:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json)
            return _Resp({"error": "overloaded"}, status=503)

    monkeypatch.setattr(httpx, "AsyncClient", _Down)
    result, saved, _ = tag_route(_openai({"content": "unused"}))
    assert result == {"error": "Vision model request failed"} and saved == ""
