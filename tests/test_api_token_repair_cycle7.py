"""Forward probes and regressions for the cycle-7 API-token repair.

The first run of this file is intentionally against the vulnerable candidate:
the security assertions below should fail before the repair is applied.  The
same tests remain as focused regressions after the fix.
"""

import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.models import ChatMessage


class _Request:
    def __init__(self, *, owner="alice", body=None, bearer=True):
        self.state = SimpleNamespace(
            api_token=bearer,
            api_token_owner=owner if bearer else None,
            api_token_scopes=["chat"] if bearer else [],
            current_user="api" if bearer else owner,
        )
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self.headers = {}
        self.query_params = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self._body = body

    async def json(self):
        return self._body


def _endpoint(router, path, method):
    for route in reversed(router.routes):
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def query(self, model):
        return _Query(self.rows_by_model.get(model, self.rows_by_model.get(None, [])))

    def close(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def add(self, value):
        return None

    def delete(self, value):
        return None


def _registered_endpoint(*, endpoint_id="ep-1", models='["safe-model"]', owner="alice"):
    return SimpleNamespace(
        id=endpoint_id,
        owner=owner,
        base_url="https://api.example.test/v1",
        is_enabled=True,
        endpoint_kind="api",
        cached_models=models,
        pinned_models=None,
        hidden_models=None,
        api_key="",
        provider_auth_id=None,
    )


def _registered_session(model="unsafe-model", endpoint_id="ep-1"):
    return SimpleNamespace(
        id="sid",
        name="chat",
        owner="alice",
        endpoint_url="https://api.example.test/v1/chat/completions",
        model=model,
        headers={},
        history=[],
        model_endpoint_id=endpoint_id,
        endpoint_provenance="registered",
    )


def _patch_validator_db(monkeypatch, endpoint_rows):
    from routes import chat_helpers

    monkeypatch.setattr(
        chat_helpers,
        "SessionLocal",
        lambda: _Db({cdb.ModelEndpoint: endpoint_rows}),
    )


def _isolated_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'repair-cycle7.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_probe_registered_bearer_session_rejects_ambiguous_or_missing_provenance(monkeypatch):
    from routes.chat_helpers import _validate_bearer_session_model

    endpoint_rows = [
        _registered_endpoint(endpoint_id="ep-a"),
        _registered_endpoint(endpoint_id="ep-b"),
    ]
    _patch_validator_db(monkeypatch, endpoint_rows)
    session = SimpleNamespace(
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="safe-model",
        model_endpoint_id=None,
        endpoint_provenance="registered",
    )

    with pytest.raises(HTTPException):
        _validate_bearer_session_model(session, owner="alice")


@pytest.mark.parametrize(
    ("label", "endpoint_rows"),
    [
        ("disabled-or-deleted", []),
        ("owner-mismatch", []),
        ("url-changed", [_registered_endpoint()]),
        ("empty-inventory", [_registered_endpoint(models='[]')]),
        ("malformed-inventory", [_registered_endpoint(models="not-json")]),
        ("hidden-model", [_registered_endpoint()]),
    ],
)
def test_registered_bearer_session_rejects_endpoint_boundary_cases(
    monkeypatch, label, endpoint_rows
):
    from routes.chat_helpers import _validate_bearer_session_model

    if label == "url-changed":
        endpoint_rows[0].base_url = "https://other.example.test/v1"
    elif label == "hidden-model":
        endpoint_rows[0].hidden_models = '["unsafe-model"]'
        endpoint_rows[0].cached_models = '["unsafe-model"]'
    elif label == "empty-inventory":
        endpoint_rows[0].pinned_models = "[]"
    elif label == "owner-mismatch":
        endpoint_rows = []  # the owner-scoped query has no visible row

    _patch_validator_db(monkeypatch, endpoint_rows)
    with pytest.raises(HTTPException):
        _validate_bearer_session_model(_registered_session(), owner="alice")


@pytest.mark.parametrize(
    "case",
    [
        "disabled",
        "deleted",
        "url-changed",
        "empty-inventory",
        "malformed-inventory",
        "hidden-model",
        "owner-mismatch",
    ],
)
def test_registered_bearer_session_rejects_durable_endpoint_boundary_cases(
    monkeypatch, tmp_path, case
):
    from routes.chat_helpers import _validate_bearer_session_model

    session_factory = _isolated_db(tmp_path)
    endpoint = cdb.ModelEndpoint(
        id="ep-1",
        name="Endpoint",
        base_url="https://api.example.test/v1",
        api_key="",
        is_enabled=True,
        owner="alice",
        endpoint_kind="api",
        cached_models='["safe-model"]',
        pinned_models=None,
        hidden_models=None,
    )
    if case == "disabled":
        endpoint.is_enabled = False
    elif case == "deleted":
        endpoint = None
    elif case == "url-changed":
        endpoint.base_url = "https://other.example.test/v1"
    elif case == "empty-inventory":
        endpoint.cached_models = "[]"
    elif case == "malformed-inventory":
        endpoint.cached_models = "not-json"
    elif case == "hidden-model":
        endpoint.cached_models = '["unsafe-model"]'
        endpoint.hidden_models = '["unsafe-model"]'
    elif case == "owner-mismatch":
        endpoint.owner = "bob"

    if endpoint is not None:
        db = session_factory()
        try:
            db.add(endpoint)
            db.commit()
        finally:
            db.close()
    monkeypatch.setattr(
        __import__("routes.chat_helpers", fromlist=["SessionLocal"]),
        "SessionLocal",
        session_factory,
    )
    with pytest.raises(HTTPException):
        _validate_bearer_session_model(_registered_session(), owner="alice")


def test_registered_bearer_session_uses_exact_id_when_base_urls_are_duplicated(
    monkeypatch, tmp_path
):
    from routes.chat_helpers import _validate_bearer_session_model

    session_factory = _isolated_db(tmp_path)
    db = session_factory()
    try:
        db.add_all(
            [
                cdb.ModelEndpoint(
                    id="ep-wrong",
                    name="Wrong duplicate",
                    base_url="https://api.example.test/v1",
                    api_key="",
                    is_enabled=True,
                    owner="alice",
                    endpoint_kind="api",
                    cached_models='["wrong-model"]',
                ),
                cdb.ModelEndpoint(
                    id="ep-1",
                    name="Exact duplicate",
                    base_url="https://api.example.test/v1",
                    api_key="",
                    is_enabled=True,
                    owner="alice",
                    endpoint_kind="api",
                    cached_models='["safe-model"]',
                ),
            ]
        )
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        __import__("routes.chat_helpers", fromlist=["SessionLocal"]),
        "SessionLocal",
        session_factory,
    )
    session = _registered_session(model="safe-model", endpoint_id="ep-1")
    assert _validate_bearer_session_model(session, owner="alice") == "safe-model"


def test_registered_bearer_session_refreshes_static_endpoint_headers(monkeypatch):
    from routes.chat_helpers import _validate_bearer_session_model

    endpoint = _registered_endpoint()
    endpoint.api_key = "current-key"
    _patch_validator_db(monkeypatch, [endpoint])
    session = _registered_session(model="safe-model")
    session.headers = {"Authorization": "Bearer stale-key"}

    assert _validate_bearer_session_model(session, owner="alice") == "safe-model"
    assert session.headers == {"Authorization": "Bearer current-key"}


def test_direct_api_key_session_preserves_compatibility_without_inventory_lookup(monkeypatch):
    from routes import chat_helpers

    def unexpected_db():
        raise AssertionError("direct API-key sessions must not consult endpoint inventory")

    monkeypatch.setattr(chat_helpers, "SessionLocal", unexpected_db)
    session = SimpleNamespace(
        endpoint_url="https://direct.example.test/v1/chat/completions",
        model="unlisted-direct-model",
        model_endpoint_id=None,
        endpoint_provenance="direct",
    )
    assert chat_helpers._validate_bearer_session_model(session, owner="alice") is None


def test_registered_local_endpoint_keeps_explicit_model_without_catalog(monkeypatch):
    from routes.chat_helpers import _validate_bearer_session_model

    endpoint = _registered_endpoint(models=None)
    endpoint.base_url = "http://localhost:8000/v1"
    endpoint.endpoint_kind = "local"
    _patch_validator_db(monkeypatch, [endpoint])
    session = _registered_session(model="operator-model")
    session.endpoint_url = "http://localhost:8000/v1/chat/completions"
    assert _validate_bearer_session_model(session, owner="alice") == "operator-model"


def test_unclassified_persisted_session_fails_closed_for_bearer_validation(monkeypatch):
    from routes.chat_helpers import _validate_bearer_session_model

    session = SimpleNamespace(
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="safe-model",
        model_endpoint_id=None,
        endpoint_provenance=None,
    )
    with pytest.raises(HTTPException):
        _validate_bearer_session_model(session, owner="alice")


def test_session_manager_round_trips_endpoint_provenance(monkeypatch, tmp_path):
    import core.session_manager as session_manager_module
    from core.session_manager import SessionManager

    session_factory = _isolated_db(tmp_path)
    monkeypatch.setattr(session_manager_module, "SessionLocal", session_factory)
    manager = SessionManager()
    session = manager.create_session(
        session_id="durable-sid",
        name="durable",
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="safe-model",
        owner="alice",
    )
    manager.set_session_endpoint_provenance(
        "durable-sid",
        model_endpoint_id="ep-1",
        endpoint_provenance="registered",
    )

    db = session_factory()
    try:
        row = db.query(cdb.Session).filter(cdb.Session.id == "durable-sid").first()
        assert row.model_endpoint_id == "ep-1"
        assert row.endpoint_provenance == "registered"
    finally:
        db.close()
    assert session.model_endpoint_id == "ep-1"
    assert session.endpoint_provenance == "registered"
    manager.sessions.clear()
    reloaded = manager.get_session("durable-sid")
    assert reloaded.model_endpoint_id == "ep-1"
    assert reloaded.endpoint_provenance == "registered"


def test_probe_bearer_patch_rejects_unlisted_model_before_persisting(monkeypatch):
    from routes import session_routes as sr

    endpoint = _registered_endpoint()
    db_session = SimpleNamespace(
        id="sid",
        owner="alice",
        model="safe-model",
        endpoint_url="https://api.example.test/v1/chat/completions",
        headers={},
        updated_at=None,
        folder=None,
    )
    _db = _Db({cdb.Session: [db_session], cdb.ModelEndpoint: [endpoint], None: [db_session]})
    monkeypatch.setattr(sr, "SessionLocal", lambda: _db)
    session = _registered_session(model="safe-model")
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        update_session_name=lambda *args, **kwargs: None,
    )
    router = sr.setup_session_routes(manager, {})
    patch_session = _endpoint(router, "/api/session/{sid}", "PATCH")

    with pytest.raises(HTTPException) as exc:
        patch_session(
            request=_Request(),
            sid="sid",
            model="unsafe-model",
            endpoint_url="https://api.example.test/v1/chat/completions",
            endpoint_id="ep-1",
        )
    assert "permitted" in str(exc.value.detail).lower()
    assert session.model == "safe-model"
    assert db_session.model == "safe-model"


def test_bearer_patch_binds_exact_endpoint_provenance(monkeypatch):
    from routes import session_routes as sr

    endpoint = _registered_endpoint()
    db_session = SimpleNamespace(
        id="sid",
        owner="alice",
        model="safe-model",
        endpoint_url="https://api.example.test/v1/chat/completions",
        headers={},
        updated_at=None,
        folder=None,
    )
    db = _Db({cdb.Session: [db_session], cdb.ModelEndpoint: [endpoint], None: [db_session]})
    monkeypatch.setattr(sr, "SessionLocal", lambda: db)
    session = _registered_session(model="safe-model")
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        update_session_name=lambda *args, **kwargs: None,
    )
    router = sr.setup_session_routes(manager, {})
    patch_session = _endpoint(router, "/api/session/{sid}", "PATCH")

    patch_session(
        request=_Request(),
        sid="sid",
        model="safe-model",
        endpoint_url="https://api.example.test/v1/chat/completions",
        endpoint_id="ep-1",
    )
    assert session.model_endpoint_id == "ep-1"
    assert session.endpoint_provenance == "registered"
    assert db_session.model_endpoint_id == "ep-1"
    assert db_session.endpoint_provenance == "registered"


@pytest.mark.asyncio
async def test_bearer_patch_then_sync_resume_uses_validated_model(monkeypatch):
    from routes import session_routes as sr
    from routes.webhook import webhook_routes as wr
    from src import llm_core

    endpoint = _registered_endpoint()
    db_session = SimpleNamespace(
        id="sid",
        owner="alice",
        model="safe-model",
        endpoint_url="https://api.example.test/v1/chat/completions",
        headers={},
        updated_at=None,
        folder=None,
    )
    db = _Db({cdb.Session: [db_session], cdb.ModelEndpoint: [endpoint], None: [db_session]})
    monkeypatch.setattr(sr, "SessionLocal", lambda: db)
    session = _registered_session(model="safe-model")
    session.add_message = lambda message: session.history.append(message)
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        update_session_name=lambda *args, **kwargs: None,
        save_sessions=lambda: None,
    )
    patch_session = _endpoint(sr.setup_session_routes(manager, {}), "/api/session/{sid}", "PATCH")
    patch_session(
        request=_Request(),
        sid="sid",
        model="safe-model",
        endpoint_url="https://api.example.test/v1/chat/completions",
        endpoint_id="ep-1",
    )
    _patch_validator_db(monkeypatch, [endpoint])

    async def fake_llm(*args, **kwargs):
        return "reply"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    sync_chat = _endpoint(
        wr.setup_webhook_routes(SimpleNamespace(), None, session_manager=manager),
        "/api/v1/chat",
        "POST",
    )
    body = SimpleNamespace(
        message="hello",
        model=None,
        session="sid",
        api_key=None,
        base_url=None,
        provider=None,
    )
    result = await sync_chat(request=_Request(), body=body)
    assert result["model"] == "safe-model"


@pytest.mark.asyncio
async def test_probe_bearer_sync_resume_revalidates_persisted_model(monkeypatch):
    from routes.webhook import webhook_routes as wr
    from src import llm_core

    session = _registered_session()
    session.history = []
    session.add_message = lambda message: session.history.append(message)
    manager = SimpleNamespace(get_session=lambda sid: session, save_sessions=lambda: None)
    _patch_validator_db(monkeypatch, [_registered_endpoint()])

    async def unexpected_llm(*args, **kwargs):
        raise AssertionError("unlisted persisted model reached the LLM")

    monkeypatch.setattr(llm_core, "llm_call_async", unexpected_llm)
    router = wr.setup_webhook_routes(
        webhook_manager=SimpleNamespace(),
        auth_manager=None,
        session_manager=manager,
    )
    sync_chat = _endpoint(router, "/api/v1/chat", "POST")
    body = SimpleNamespace(
        message="hello",
        model=None,
        session="sid",
        api_key=None,
        base_url=None,
        provider=None,
    )

    with pytest.raises(HTTPException):
        await sync_chat(request=_Request(), body=body)


@pytest.mark.asyncio
async def test_probe_bearer_rewrite_revalidates_before_streaming(monkeypatch):
    from routes import chat_routes as cr

    session = _registered_session()
    session.history = []
    manager = SimpleNamespace(get_session=lambda sid: session, save_sessions=lambda: None)
    _patch_validator_db(monkeypatch, [_registered_endpoint()])
    monkeypatch.setattr(cr, "_verify_session_owner", lambda *args, **kwargs: None)
    router = cr.setup_chat_routes(manager, None, None, None, None, None, webhook_manager=None)
    rewrite = _endpoint(router, "/api/rewrite", "POST")

    with pytest.raises(HTTPException):
        await rewrite(
            request=_Request(
                body={
                    "session_id": "sid",
                    "original_text": "old",
                    "instruction": "shorter",
                }
            )
        )


@pytest.mark.asyncio
async def test_probe_bearer_compaction_aliases_revalidate_before_llm(monkeypatch):
    from routes import session_routes as sr
    from routes.history import history_routes as hr
    from core.models import ChatMessage
    from src import llm_core, model_context

    session = _registered_session()
    session.history = [ChatMessage("user", f"message {i}") for i in range(6)]
    session.get_context_messages = lambda: [{"role": "user", "content": "message"}]
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        replace_messages=lambda *args: True,
        save_sessions=lambda: None,
    )
    _patch_validator_db(monkeypatch, [_registered_endpoint()])
    monkeypatch.setattr(sr, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(hr, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "_reject_compact_during_active_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(hr, "_reject_compact_during_active_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "SessionLocal", lambda: _Db({}))
    monkeypatch.setattr(hr, "SessionLocal", lambda: _Db({}))
    monkeypatch.setattr(model_context, "get_context_length", lambda *args, **kwargs: 4096)

    async def compact_llm(*args, **kwargs):
        return "summary"

    monkeypatch.setattr(llm_core, "llm_call_async", compact_llm)

    session_router = sr.setup_session_routes(manager, {})
    history_router = hr.setup_history_routes(manager)
    session_compact = _endpoint(session_router, "/api/session/{session_id}/compact", "POST")
    history_compact = _endpoint(history_router, "/api/session/{session_id}/compact", "POST")

    with pytest.raises(HTTPException):
        await session_compact(request=_Request(), session_id="sid")
    with pytest.raises(HTTPException):
        await history_compact(request=_Request(), session_id="sid")


@pytest.mark.asyncio
async def test_probe_bearer_gallery_json_reference_serves_owned_binary(monkeypatch, tmp_path):
    # app.py normally calls load_dotenv at import time. Replace that call in
    # this isolated probe so the probe never reads any .env* file.
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    if "app" in sys.modules:
        app = sys.modules["app"]
    else:
        import app  # noqa: PLC0415

    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"owned image")
    row = SimpleNamespace(filename="image.png", owner="alice")
    monkeypatch.setattr(app, "resolve_generated_image_path", lambda filename: image_path)
    monkeypatch.setattr(cdb, "SessionLocal", lambda: _Db({cdb.GalleryImage: [row]}))

    response = await app.serve_generated_image("image.png", _Request())
    assert response.path == str(image_path)

    cookie_response = await app.serve_generated_image(
        "image.png", _Request(owner="alice", bearer=False)
    )
    assert cookie_response.path == str(image_path)
    with pytest.raises(HTTPException):
        await app.serve_generated_image(
            "image.png", _Request(owner="bob", bearer=False)
        )
