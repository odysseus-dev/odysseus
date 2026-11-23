"""Tests for the image-op progress contract (HARNESS-owned).

The 7011 side accepts an optional client-supplied ``request_id`` on heavy image
ops and exposes ``GET /api/image/edit-progress`` which proxies the provider's
``/v1/images/progress/{request_id}`` route. When the provider has no such route
the endpoint returns ``{"available": false}`` so the client keeps an
indeterminate bar.
"""
import asyncio
from types import SimpleNamespace

import httpx

import routes.gallery_routes as gallery


def _route(path):
    return next(r for r in gallery.setup_gallery_routes().routes if r.path == path)


class _Req:
    def __init__(self, query):
        self.query_params = query


def _fake_db():
    class Db:
        def close(self):
            pass

        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    return Db()


def test_edit_progress_requires_request_id(monkeypatch):
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    route = _route("/api/image/edit-progress")
    try:
        asyncio.run(route.endpoint(_Req({})))
    except Exception as exc:  # FastAPI HTTPException
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("expected a 400 for a missing request_id")


def test_edit_progress_rejects_an_unsafe_request_id(monkeypatch):
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    monkeypatch.setattr(gallery, "SessionLocal", lambda: _fake_db())
    route = _route("/api/image/edit-progress")
    try:
        asyncio.run(route.endpoint(_Req({"request_id": "../secret"})))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("expected a 400 for an unsafe request_id")


def test_edit_progress_returns_provider_payload(monkeypatch):
    endpoint = SimpleNamespace(base_url="http://100.69.120.65:8188/v1", api_key="k")
    monkeypatch.setattr(gallery, "SessionLocal", lambda: _fake_db())
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    monkeypatch.setattr(gallery, "_first_visible_image_endpoint", lambda *a: endpoint)
    seen = {}

    def respond(request):
        seen["url"] = str(request.url)
        # Live seneca contract stages: loading | editing | compositing | done | error.
        return httpx.Response(
            200,
            json={"stage": "editing", "step": 3, "steps": 8, "eta_s": 42.0, "progress": 0.375},
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: client(transport=httpx.MockTransport(respond), **k))
    out = asyncio.run(_route("/api/image/edit-progress").endpoint(_Req({"request_id": "abc-123"})))
    assert out["available"] is True
    assert out["stage"] == "editing"  # passed through unchanged
    assert out["step"] == 3 and out["steps"] == 8
    assert seen["url"].endswith("/images/progress/abc-123")


def test_edit_progress_available_false_when_provider_has_no_route(monkeypatch):
    endpoint = SimpleNamespace(base_url="http://100.69.120.65:8188/v1", api_key="k")
    monkeypatch.setattr(gallery, "SessionLocal", lambda: _fake_db())
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    monkeypatch.setattr(gallery, "_first_visible_image_endpoint", lambda *a: endpoint)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **k: client(transport=httpx.MockTransport(lambda req: httpx.Response(404)), **k),
    )
    out = asyncio.run(_route("/api/image/edit-progress").endpoint(_Req({"request_id": "abc"})))
    assert out["available"] is False


def test_edit_progress_available_false_when_unreachable(monkeypatch):
    endpoint = SimpleNamespace(base_url="http://100.69.120.65:8188/v1", api_key="k")
    monkeypatch.setattr(gallery, "SessionLocal", lambda: _fake_db())
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    monkeypatch.setattr(gallery, "_first_visible_image_endpoint", lambda *a: endpoint)

    class Boom:
        async def __aenter__(self):
            raise httpx.ConnectError("nope")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Boom())
    out = asyncio.run(_route("/api/image/edit-progress").endpoint(_Req({"request_id": "abc"})))
    assert out["available"] is False
