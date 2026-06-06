import json
import sys
from types import SimpleNamespace

_endpoint_resolver = sys.modules.get("src.endpoint_resolver")
if _endpoint_resolver is not None and not getattr(_endpoint_resolver, "__file__", None):
    sys.modules.pop("src.endpoint_resolver", None)
    sys.modules.pop("routes.model_routes", None)
    sys.modules.pop("routes.chat_routes", None)

from routes import chat_routes


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        return self

    def all(self):
        return list(self.rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def query(self, model):
        return _FakeQuery(self.rows)

    def close(self):
        self.closed = True


def _session(model="qwen3.5:latest", endpoint_url="http://localhost:11434/v1/chat/completions"):
    return SimpleNamespace(model=model, endpoint_url=endpoint_url)


def _endpoint(base_url, model_type="image", models=None):
    cached_models = None if models is None else json.dumps(models)
    return SimpleNamespace(
        base_url=base_url,
        model_type=model_type,
        is_enabled=True,
        cached_models=cached_models,
    )


def test_image_model_prefix_routes_to_image_generation_without_endpoint_lookup(monkeypatch):
    def fail_if_called():
        raise AssertionError("prefixed image models should not need a DB lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)

    assert chat_routes._is_image_generation_session(_session(model="dall-e-3"))


def test_image_endpoint_does_not_catch_text_model_on_different_path(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1/images", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert not chat_routes._is_image_generation_session(_session())
    assert db.closed


def test_image_endpoint_cache_must_contain_selected_model(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert not chat_routes._is_image_generation_session(_session(model="qwen3.5:latest"))


def test_matching_image_endpoint_routes_selected_image_model(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert chat_routes._is_image_generation_session(_session(model="sdxl-local"))
