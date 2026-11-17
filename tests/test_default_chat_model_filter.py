"""Issue #2137: never preselect an embedding or other non-chat model."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import ModelEndpoint
from routes import model_routes, prefs_routes
from tests.helpers.database import disposable_database


@pytest.fixture
def defaults_api(monkeypatch, tmp_path):
    defaults = {}
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(model_routes, "SessionLocal", factory)
        monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
        monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: defaults)
        app = FastAPI()
        app.state.auth_manager = SimpleNamespace(is_admin=lambda owner: False)

        @app.middleware("http")
        async def owner(request, call_next):
            request.state.current_user = "alice"
            return await call_next(request)

        app.include_router(model_routes.setup_model_routes(model_discovery=None))
        with TestClient(app) as client:
            yield client, factory, defaults


def _endpoint(factory, *, cached=(), pinned=(), hidden=(), owner="alice", endpoint_id="ep"):
    with factory() as db:
        db.add(ModelEndpoint(
            id=endpoint_id, name="Fixture", base_url="http://model.invalid/v1", owner=owner,
            is_enabled=True, cached_models=json.dumps(cached),
            pinned_models=json.dumps(pinned), hidden_models=json.dumps(hidden),
        ))
        db.commit()


@pytest.mark.parametrize("non_chat", [
    "text-embedding-3-small", "nomic-embed-text:latest", "jina-reranker-v2-base",
    "tts-1", "whisper-1", "gpt-image-1",
])
def test_automatic_default_skips_non_chat_models(defaults_api, non_chat):
    client, factory, _ = defaults_api
    _endpoint(factory, cached=[non_chat, "chat-model"])
    assert client.get("/api/default-chat").json()["model"] == "chat-model"


def test_non_chat_saved_default_falls_back_to_visible_pinned_chat(defaults_api):
    client, factory, defaults = defaults_api
    _endpoint(factory, cached=["text-embedding-3-small", "hidden-chat"],
              pinned=["jina-reranker-v2-base", "pinned-chat"], hidden=["hidden-chat"])
    defaults.update(default_endpoint_id="ep", default_model="text-embedding-3-small")
    assert client.get("/api/default-chat").json()["model"] == "pinned-chat"


def test_no_chat_candidate_leaves_model_empty(defaults_api):
    client, factory, defaults = defaults_api
    _endpoint(factory, cached=["text-embedding-3-small", "hidden-chat"],
              pinned=["jina-reranker-v2-base"], hidden=["hidden-chat"])
    defaults.update(default_endpoint_id="ep", default_model="text-embedding-3-small")
    result = client.get("/api/default-chat").json()
    assert result == {"endpoint_id": "ep", "endpoint_url": "http://model.invalid/v1/chat/completions", "model": ""}


def test_valid_explicit_model_survives_stale_catalog(defaults_api):
    client, factory, defaults = defaults_api
    _endpoint(factory, cached=["text-embedding-3-small", "other-chat"])
    defaults.update(default_endpoint_id="ep", default_model="my-custom-chat")
    assert client.get("/api/default-chat").json()["model"] == "my-custom-chat"


def test_model_filter_preserves_owner_scope(defaults_api):
    client, factory, defaults = defaults_api
    _endpoint(factory, owner="bob", endpoint_id="bobs-endpoint", cached=["bobs-chat"])
    _endpoint(factory, cached=["text-embedding-3-small", "alices-chat"])
    defaults.update(default_endpoint_id="bobs-endpoint")
    result = client.get("/api/default-chat").json()
    assert result["endpoint_id"] == "ep"
    assert result["model"] == "alices-chat"
