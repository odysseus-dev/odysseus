import sys
for mod_name in ["src.endpoint_resolver", "src.database", "core.database"]:
    _mod = sys.modules.get(mod_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(mod_name, None)

import json
from types import SimpleNamespace

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules

clear_fake_endpoint_resolver_modules("routes.chat_routes")

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
        id="endpoint-id",
        base_url=base_url,
        model_type=model_type,
        is_enabled=True,
        cached_models=cached_models,
        hidden_models=None,
    )


class _FakeRecoverQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeRecoverDb:
    def __init__(self, endpoints, db_session):
        self.endpoints = endpoints
        self.db_session = db_session
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        if model is chat_routes.ModelEndpoint:
            return _FakeRecoverQuery(self.endpoints)
        return _FakeRecoverQuery([self.db_session])

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


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


def test_responses_url_matches_saved_openai_endpoint():
    assert chat_routes._session_url_matches_endpoint(
        "https://api.openai.com/v1/responses",
        "https://api.openai.com/v1",
    )


def test_recover_repairs_embedding_session_to_named_chat_model(monkeypatch):
    sess = _session(
        model="text-embedding-ada-002",
        endpoint_url="https://api.openai.com/v1/responses",
    )
    sess.name = "gpt-5.5-pro 00:40:31"
    endpoint = _endpoint(
        "https://api.openai.com/v1",
        model_type="llm",
        models=[
            "text-embedding-ada-002",
            "whisper-1",
            "gpt-5.5",
            "gpt-5.5-pro",
        ],
    )
    db_session = SimpleNamespace(model=sess.model, updated_at=None)
    db = _FakeRecoverDb([endpoint], db_session)
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert chat_routes._recover_empty_session_model(sess, "sid")
    assert sess.model == "gpt-5.5-pro"
    assert db_session.model == "gpt-5.5-pro"
    assert db.committed
    assert db.closed


def test_image_models_are_not_repaired_as_bad_chat_models():
    assert not chat_routes._session_model_needs_chat_repair("dall-e-3")


def test_openai_codex_models_are_not_repaired_as_bad_chat_models():
    assert not chat_routes._session_model_needs_chat_repair("gpt-5.5-codex")
