"""M1 tests for the laya integration: config flag, fail-open service, client
transient-retry semantics, and the admin-gated health route.

Run: python -m pytest tests/test_laya_service.py
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.laya.config import LayaConfig
from services.laya.client import LayaClient, LayaUnavailable, LayaInvalidResponse
from services.laya.service import LayaService
from routes.laya_routes import setup_laya_routes


# ----------------------------- config / flag -----------------------------

def test_disabled_by_default(monkeypatch):
    for var in ("LAYA_ENABLED", "LAYA_URL", "LAYA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg = LayaConfig.from_env()
    assert cfg.enabled is False
    assert cfg.url.startswith("http")


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("LAYA_ENABLED", "true")
    monkeypatch.setenv("LAYA_URL", "http://laya:8000/")  # trailing slash trimmed
    monkeypatch.setenv("LAYA_API_KEY", "sekret")
    monkeypatch.setenv("LAYA_RETRIES", "5")
    cfg = LayaConfig.from_env()
    assert cfg.enabled is True
    assert cfg.url == "http://laya:8000"
    assert cfg.api_key == "sekret"
    assert cfg.retries == 5


# ----------------------------- service (fail-open) -----------------------------

class _FakeClient:
    """Stand-in LayaClient that records whether it was called."""

    def __init__(self, *, result=None, exc=None):
        self.result = result or {}
        self.exc = exc
        self.calls = 0

    async def health(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result

    async def aclose(self):
        pass


async def test_disabled_service_never_touches_network():
    client = _FakeClient(result={"status": "ok"})
    service = LayaService(config=LayaConfig(enabled=False), client=client)
    health = await service.health()
    assert health.enabled is False and health.reachable is False
    assert client.calls == 0  # disabled => no network


async def test_health_reachable():
    client = _FakeClient(result={"status": "ok", "models": ["english"]})
    service = LayaService(config=LayaConfig(enabled=True), client=client)
    health = await service.health()
    assert health.enabled is True and health.reachable is True
    assert health.detail["models"] == ["english"]
    assert health.error is None


async def test_health_fail_open_when_unreachable():
    client = _FakeClient(exc=LayaUnavailable("connection refused"))
    service = LayaService(config=LayaConfig(enabled=True), client=client)
    health = await service.health()  # must NOT raise
    assert health.enabled is True and health.reachable is False
    assert "connection refused" in (health.error or "")


async def test_health_fail_open_on_unexpected_error():
    client = _FakeClient(exc=RuntimeError("boom"))
    service = LayaService(config=LayaConfig(enabled=True), client=client)
    health = await service.health()  # defensive catch-all, still no raise
    assert health.reachable is False and "boom" in (health.error or "")


# ----------------------------- client (retry semantics) -----------------------------

def _cfg(**kw):
    base = dict(enabled=True, url="http://laya.test", timeout=1.0, retries=2, backoff=0.0)
    base.update(kw)
    return LayaConfig(**base)


async def test_client_retries_transient_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"busy": True})
        return httpx.Response(200, json={"status": "ok"})

    client = LayaClient(_cfg(), transport=httpx.MockTransport(handler))
    out = await client.health()
    assert out == {"status": "ok"}
    assert calls["n"] == 2  # one transient retry
    await client.aclose()


async def test_client_gives_up_after_retries():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    client = LayaClient(_cfg(retries=2), transport=httpx.MockTransport(handler))
    with pytest.raises(LayaUnavailable):
        await client.health()
    assert calls["n"] == 3  # initial + 2 retries
    await client.aclose()


async def test_client_does_not_retry_4xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    client = LayaClient(_cfg(retries=3), transport=httpx.MockTransport(handler))
    with pytest.raises(LayaInvalidResponse):
        await client.health()
    assert calls["n"] == 1  # 4xx is terminal, not retried
    await client.aclose()


async def test_client_sends_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    client = LayaClient(_cfg(api_key="sekret"), transport=httpx.MockTransport(handler))
    await client.health()
    assert seen["auth"] == "Bearer sekret"
    await client.aclose()


async def test_client_systemone_posts_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        seen["url"] = str(request.url)
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"answers": {}})

    client = LayaClient(_cfg(), transport=httpx.MockTransport(handler))
    await client.systemone({"body": "hi"}, {"q": {"type": "noul", "instructions": "?"}})
    assert seen["url"].endswith("/v1/systemone")
    assert seen["body"]["state"] == {"body": "hi"}
    assert "questions" in seen["body"]
    await client.aclose()


# ----------------------------- route (admin gate) -----------------------------

def _app_with_route(monkeypatch, *, service):
    monkeypatch.setattr("routes.laya_routes.get_laya_service", lambda: service)
    app = FastAPI()
    app.include_router(setup_laya_routes())
    return app


def test_health_route_requires_admin(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")  # and no auth_manager on app.state
    app = _app_with_route(monkeypatch, service=LayaService(config=LayaConfig(enabled=False)))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/laya/health")
    assert resp.status_code == 403


def test_health_route_ok_when_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")  # bypass admin gate
    service = LayaService(config=LayaConfig(enabled=False), client=_FakeClient())
    app = _app_with_route(monkeypatch, service=service)
    client = TestClient(app)
    resp = client.get("/api/laya/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": False, "reachable": False, "detail": {}, "error": None}
