"""Regression coverage for the cycle-6 API-token repair boundaries."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb


class _Request:
    def __init__(self, *, scopes=("chat",), bearer=True, owner="alice"):
        self.state = SimpleNamespace(
            api_token=bearer,
            api_token_owner=owner if bearer else None,
            api_token_scopes=list(scopes),
            current_user="api" if bearer else owner,
        )
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self.headers = {"authorization": "Bearer ody_test"} if bearer else {}
        self.client = SimpleNamespace(host="127.0.0.1")


class _EndpointDb:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.endpoint

    def all(self):
        return [self.endpoint] if self.endpoint is not None else []

    def close(self):
        return None


class _StateInjector:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"x-api-token") == b"1":
                scope["state"] = {
                    "api_token": True,
                    "api_token_owner": headers.get(b"x-api-owner", b"").decode() or None,
                    "api_token_scopes": [
                        value for value in headers.get(b"x-api-scopes", b"").decode().split(",")
                        if value
                    ],
                    "current_user": "api",
                }
            else:
                scope["state"] = {
                    "api_token": False,
                    "current_user": headers.get(b"x-user", b"").decode() or None,
                }
        await self.app(scope, receive, send)


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://cycle6.test",
    )


def _endpoint(router, path, method):
    for route in reversed(router.routes):
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _isolated_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cycle6-repair.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.mark.asyncio
async def test_gallery_asgi_scope_and_non_bearer_boundaries(monkeypatch, tmp_path):
    from routes.gallery import gallery_routes
    from core.database import GalleryImage

    db_session = _isolated_db(tmp_path)
    image_dir = tmp_path / "generated-images"
    monkeypatch.setattr(gallery_routes, "SessionLocal", db_session)
    monkeypatch.setattr(gallery_routes, "GENERATED_IMAGES_DIR", image_dir)
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", image_dir)

    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    client = _client(_StateInjector(app))

    async with client:
        response = await client.post(
            "/api/gallery/upload",
            files={"file": ("photo.png", b"not-a-real-image", "image/png")},
            headers={
                "x-api-token": "1",
                "x-api-owner": "alice",
                "x-api-scopes": "todos:read",
                "authorization": "Bearer ody_test",
            },
        )
        assert response.status_code == 403

        response = await client.post(
            "/api/gallery/upload",
            files={"file": ("photo.png", b"not-a-real-image", "image/png")},
            headers={
                "x-api-token": "1",
                "x-api-owner": "alice",
                "x-api-scopes": "chat",
                "authorization": "Bearer ody_test",
            },
        )
        assert response.status_code == 200, response.text

        for path, method in (
            ("/api/gallery/ai-tag-batch", "post"),
            ("/api/gallery/unknown/ai-tag", "post"),
            ("/api/image/inpaint", "post"),
        ):
            response = await getattr(client, method)(
                path,
                headers={
                    "x-api-token": "1",
                    "x-api-owner": "alice",
                    "x-api-scopes": "chat",
                    "authorization": "Bearer ody_test",
                },
            )
            assert response.status_code == 403, (path, response.text)

    db = db_session()
    try:
        row = db.query(GalleryImage).first()
        assert row is not None
        assert row.owner == "alice"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_gallery_cookie_ai_tag_uses_fake_provider_and_bearer_never_reaches_it(
    monkeypatch,
    tmp_path,
):
    from routes.gallery import gallery_routes
    from core.database import GalleryImage

    db_session = _isolated_db(tmp_path)
    image_dir = tmp_path / "gallery"
    image_dir.mkdir()
    (image_dir / "image.png").write_bytes(b"fake-image")
    db = db_session()
    try:
        db.add(GalleryImage(
            id="image-1",
            filename="image.png",
            prompt="photo",
            model="imported",
            owner="alice",
            file_hash="hash",
            file_size=10,
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(gallery_routes, "SessionLocal", db_session)
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", image_dir)
    monkeypatch.setattr(
        "src.document_processor._load_vl_settings",
        lambda: {"vision_enabled": True, "vision_model": "vision-model"},
    )
    monkeypatch.setattr(
        "src.document_processor._resolve_vl_model",
        lambda configured, owner=None: (
            "https://vision.example/v1/chat/completions",
            configured,
            {},
        ),
    )

    provider_calls = []

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "photo, test"}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            provider_calls.append((args, kwargs))
            return _Response()

    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    client = _client(_StateInjector(app))
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async with client:
        cookie_response = await client.post(
            "/api/gallery/image-1/ai-tag",
            headers={"x-user": "alice"},
        )
        assert cookie_response.status_code == 200, cookie_response.text
        assert provider_calls
        before_bearer = len(provider_calls)

        bearer_response = await client.post(
            "/api/gallery/image-1/ai-tag",
            headers={
                "x-api-token": "1",
                "x-api-owner": "alice",
                "x-api-scopes": "chat",
                "authorization": "Bearer ody_test",
            },
        )
        assert bearer_response.status_code == 403
        assert len(provider_calls) == before_bearer


def test_bearer_model_selection_rejects_hidden_unlisted_and_empty_inventory():
    from routes.model_routes import _validate_bearer_model_selection

    endpoint = SimpleNamespace(
        base_url="https://api.example.test/v1",
        endpoint_kind="api",
        cached_models=json.dumps(["cached-model", "hidden-model"]),
        pinned_models=json.dumps(["allowed-model"]),
        hidden_models=json.dumps(["hidden-model"]),
    )
    assert _validate_bearer_model_selection(endpoint, "allowed-model") == "allowed-model"
    for model in ("cached-model", "hidden-model", "missing-model"):
        with pytest.raises(HTTPException) as exc:
            _validate_bearer_model_selection(endpoint, model)
        assert exc.value.status_code == 400

    endpoint.pinned_models = "[]"
    assert _validate_bearer_model_selection(endpoint, "", allow_empty=True) == ""
    with pytest.raises(HTTPException):
        _validate_bearer_model_selection(endpoint, "cached-model")


def test_bearer_default_chat_empty_pin_does_not_fall_back_to_cache(monkeypatch):
    from routes import model_routes
    from routes import prefs_routes

    endpoint = SimpleNamespace(
        id="ep",
        base_url="https://api.example.test/v1",
        endpoint_kind="api",
        is_enabled=True,
        cached_models=json.dumps(["cached-model"]),
        pinned_models="[]",
        hidden_models=None,
    )
    db = _EndpointDb(endpoint)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {
        "default_endpoint_id": "ep",
        "default_model": "",
        "share_defaults_with_users": False,
    })
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: {})
    route = _endpoint(model_routes.setup_model_routes(None), "/api/default-chat", "GET")

    result = route(_Request())
    assert result == {
        "endpoint_id": "ep",
        "endpoint_url": "https://api.example.test/v1/chat/completions",
        "model": "",
    }


def test_bearer_session_model_is_checked_against_endpoint_inventory(monkeypatch):
    from routes import session_routes

    endpoint = SimpleNamespace(
        id="ep",
        is_enabled=True,
        base_url="https://api.example.test/v1",
        api_key=None,
        endpoint_kind="api",
        cached_models=json.dumps(["provider-model"]),
        pinned_models=json.dumps(["allowed-model"]),
        hidden_models=None,
    )
    db = _EndpointDb(endpoint)
    monkeypatch.setattr(session_routes, "SessionLocal", lambda: db)
    manager = SimpleNamespace(create_session=lambda **kwargs: pytest.fail("session was created"))
    route = _endpoint(
        session_routes.setup_session_routes(manager, {}),
        "/api/session",
        "POST",
    )

    with pytest.raises(HTTPException) as exc:
        route(
            request=_Request(),
            name="chat",
            endpoint_url="",
            model="provider-model",
            rag=None,
            skip_validation="true",
            api_key="",
            endpoint_id="ep",
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_sync_chat_uses_cached_model_and_skips_provider_runtime_resolution(monkeypatch):
    from routes import webhook_routes
    from src import chatgpt_subscription, llm_core

    endpoint = SimpleNamespace(
        owner="alice",
        is_enabled=True,
        created_at=1,
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=None,
        provider_auth_id="provider-auth",
        endpoint_kind="api",
        cached_models=json.dumps(["cached-model"]),
        pinned_models=json.dumps(["allowed-model"]),
        hidden_models=None,
    )
    monkeypatch.setattr(webhook_routes, "SessionLocal", lambda: _EndpointDb(endpoint))
    runtime_calls = []
    monkeypatch.setattr(
        chatgpt_subscription,
        "resolve_runtime_credentials",
        lambda *args, **kwargs: runtime_calls.append((args, kwargs)) or pytest.fail(
            "bearer sync resolved provider credentials"
        ),
    )
    llm_calls = []

    async def fake_llm(*args, **kwargs):
        llm_calls.append(kwargs)
        return "reply"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)

    class _Session:
        def __init__(self, **kwargs):
            self.endpoint_url = kwargs["endpoint_url"]
            self.model = kwargs["model"]
            self.headers = {}
            self.history = []

        def add_message(self, message):
            self.history.append(message)

    manager = SimpleNamespace(
        create_session=lambda **kwargs: _Session(**kwargs),
        save_sessions=lambda: None,
    )
    router = webhook_routes.setup_webhook_routes(
        SimpleNamespace(fire_and_forget=lambda *args, **kwargs: None),
        None,
        session_manager=manager,
    )
    route = _endpoint(router, "/api/v1/chat", "POST")
    body = SimpleNamespace(
        message="hello",
        model=None,
        session=None,
        api_key=None,
        base_url=None,
        provider=None,
    )

    result = await route(request=_Request(), body=body)
    assert result["model"] == "allowed-model"
    assert runtime_calls == []
    assert llm_calls[0]["allow_live_probes"] is False


def test_provider_runtime_guard_is_no_live_without_opening_credentials_db(monkeypatch):
    from src import chatgpt_subscription

    monkeypatch.setattr(
        chatgpt_subscription,
        "_database_handles",
        lambda: pytest.fail("no-live provider guard opened the credentials database"),
    )
    with pytest.raises(chatgpt_subscription.ChatGPTSubscriptionReauthRequired):
        chatgpt_subscription.resolve_runtime_credentials(
            "provider-auth",
            owner="alice",
            allow_live_probes=False,
        )


def test_foreground_descriptors_propagate_no_live_to_provider_endpoint_resolution(monkeypatch):
    from src import chatgpt_subscription, endpoint_resolver, foreground_model_routing

    endpoint = SimpleNamespace(
        id="ep",
        name="Subscription",
        is_enabled=True,
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=None,
        provider_auth_id="provider-auth",
        endpoint_kind="api",
        cached_models=json.dumps(["allowed-model"]),
        pinned_models=json.dumps(["allowed-model"]),
        hidden_models=None,
    )
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", lambda: _EndpointDb(endpoint))
    monkeypatch.setattr(
        chatgpt_subscription,
        "resolve_runtime_credentials",
        lambda *args, **kwargs: pytest.fail("foreground descriptor resolved provider credentials"),
    )

    descriptors = foreground_model_routing.build_foreground_route_descriptors(
        "https://chatgpt.com/backend-api/codex/responses",
        "allowed-model",
        {},
        owner="alice",
        policy=foreground_model_routing.ForegroundModelPolicy(),
        allow_live_probes=False,
    )
    assert descriptors[0]["endpoint_label"] in {"Subscription", "Selected route"}


@pytest.mark.asyncio
async def test_codex_owner_bridge_is_asgi_compatible_with_real_bearer_header():
    from routes import codex_routes
    from src.auth_helpers import is_bearer_principal, require_user

    memory_router = APIRouter(prefix="/api/memory")

    @memory_router.get("")
    async def memory_list(request):
        return {
            "owner": require_user(request),
            "bearer": is_bearer_principal(request),
            "authorization": request.headers.get("authorization"),
        }

    app = FastAPI()
    app.include_router(codex_routes.setup_codex_routes(memory_router=memory_router))
    async with _client(_StateInjector(app)) as client:
        response = await client.get(
            "/api/codex/memory",
            headers={
                "x-api-token": "1",
                "x-api-owner": "alice",
                "x-api-scopes": "memory:read",
                "authorization": "Bearer ody_test",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "owner": "alice",
        "bearer": False,
        "authorization": None,
    }


@pytest.mark.asyncio
async def test_codex_owner_bridge_directly_restores_scope_headers():
    from starlette.requests import Request

    from routes.codex_routes import _as_owner
    from src.auth_helpers import is_bearer_principal, require_user

    original_headers = [
        (b"authorization", b"Bearer ody_test"),
        (b"x-test", b"1"),
    ]
    scope = {"type": "http", "headers": original_headers, "state": {
        "api_token": True,
        "api_token_owner": "alice",
        "api_token_scopes": ["memory:read"],
        "current_user": "api",
    }}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    assert request.headers.get("authorization") == "Bearer ody_test"

    async def nested(req):
        assert not is_bearer_principal(req)
        assert req.headers.get("authorization") is None
        assert require_user(req) == "alice"
        return "ok"

    assert await _as_owner(request, "alice", nested, request) == "ok"
    assert scope["headers"] == original_headers
    assert request.headers.get("authorization") == "Bearer ody_test"
    assert is_bearer_principal(request)
    assert request.state.api_token is True
    assert request.state.current_user == "api"


def test_compare_direct_model_gate_rejects_unlisted_bearer_models(monkeypatch):
    from routes import compare_routes

    endpoint = SimpleNamespace(
        id="ep",
        base_url="https://api.example.test/v1",
        api_key=None,
        endpoint_kind="api",
        cached_models=json.dumps(["cached-model"]),
        pinned_models=json.dumps(["allowed-model"]),
        hidden_models=None,
        is_enabled=True,
    )
    monkeypatch.setattr(compare_routes, "SessionLocal", lambda: _EndpointDb(endpoint))
    manager = SimpleNamespace(
        create_session=lambda **kwargs: pytest.fail("comparison session was created"),
    )
    route = _endpoint(compare_routes.setup_compare_routes(manager), "/api/compare/start", "POST")

    with pytest.raises(HTTPException) as exc:
        route(
            request=_Request(),
            prompt="compare",
            model_a="cached-model",
            model_b="allowed-model",
            endpoint_a="",
            endpoint_b="",
            endpoint_a_id="ep",
            endpoint_b_id="ep",
            is_blind="true",
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_compare_aliases_run_chat_scope_dependency(monkeypatch):
    from routes import compare_routes

    router = compare_routes.setup_compare_routes(SimpleNamespace())
    app = FastAPI()
    app.include_router(router)
    headers = {
        "x-api-token": "1",
        "x-api-owner": "alice",
        "x-api-scopes": "todos:read",
        "authorization": "Bearer ody_test",
    }
    async with _client(_StateInjector(app)) as client:
        requests = [
            client.post("/api/compare/start", data={"prompt": "x", "model_a": "a", "model_b": "b", "endpoint_a": "https://a.example", "endpoint_b": "https://b.example"}, headers=headers),
            client.post("/api/compare/record", json={"prompt": "x", "models": ["a", "b"], "winner": "tie"}, headers=headers),
            client.get("/api/compare/history", headers=headers),
            client.post("/api/compare/abc/vote", data={"winner": "tie"}, headers=headers),
            client.delete("/api/compare/abc", headers=headers),
        ]
        responses = await __import__("asyncio").gather(*requests)
    assert all(response.status_code == 403 for response in responses)


@pytest.mark.asyncio
async def test_bearer_stream_skips_intent_classifier_and_tool_preprocessing(monkeypatch):
    from routes import chat_helpers, chat_routes
    from tests.test_foreground_model_routing import _chat_stream_endpoint

    calls = []
    captured = {}
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        capture_completion=True,
        capture_context=True,
    )
    monkeypatch.setattr(
        chat_routes,
        "_classify_tool_intent",
        lambda message: calls.append(message) or pytest.fail("bearer intent classifier ran"),
    )

    class _EmptyDb:
        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr(chat_helpers, "SessionLocal", _EmptyDb)
    request = SimpleNamespace(
        headers={"authorization": "Bearer ody_test"},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        state=SimpleNamespace(
            api_token=True,
            api_token_owner="alice",
            api_token_scopes=["chat"],
            current_user="api",
        ),
        _form={
            "message": "create a todo and use tools",
            "session": "session-1",
            "mode": "chat",
        },
    )

    async def form():
        return request._form

    request.form = form
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert calls == []
    assert "chat" in captured
    assert captured["build_context"]["allow_tool_preprocessing"] is False
