"""Tests for routes/nexus_routes.py — proxy endpoint error handling."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

import routes.nexus_routes as nexus_mod
from routes.nexus_routes import _proxy_get, setup_nexus_routes
from fastapi import HTTPException


# ── Fake httpx helpers ─────────────────────────────────────────────────
# The conftest stubs httpx with MagicMock when it is not installed.  The
# `except httpx.ConnectError:` clause in _proxy_get needs real exception
# classes, so we define them here and monkeypatch them onto the module.

class _ConnectError(Exception):
    pass


class _TimeoutException(Exception):
    pass


class _FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


class _FakeClient:
    """Async-context-manager fake for httpx.AsyncClient."""

    def __init__(self, response=None, error=None, **kwargs):
        self._response = response or _FakeResponse()
        self._error = error
        self._last_url = None
        self._last_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, params=None):
        self._last_url = url
        self._last_params = params
        if self._error:
            raise self._error
        return self._response


def _patch_httpx(monkeypatch, *, response=None, error=None):
    """Wire up a fake AsyncClient and real exception types on nexus_mod.httpx."""
    monkeypatch.setattr(
        nexus_mod.httpx, "AsyncClient",
        lambda **kw: _FakeClient(response=response, error=error),
    )
    monkeypatch.setattr(nexus_mod.httpx, "ConnectError", _ConnectError)
    monkeypatch.setattr(nexus_mod.httpx, "TimeoutException", _TimeoutException)


# ── _proxy_get unit tests ──────────────────────────────────────────────

class TestProxyGet:
    """Error handling and success paths for the _proxy_get helper."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self, monkeypatch):
        _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))
        result = await _proxy_get("http://sidecar/health")
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_success_with_params(self, monkeypatch):
        client_holder = {}

        def _factory(**kw):
            c = _FakeClient(response=_FakeResponse(200, []))
            client_holder["c"] = c
            return c

        monkeypatch.setattr(nexus_mod.httpx, "AsyncClient", _factory)
        monkeypatch.setattr(nexus_mod.httpx, "ConnectError", _ConnectError)
        monkeypatch.setattr(nexus_mod.httpx, "TimeoutException", _TimeoutException)

        await _proxy_get("http://sidecar/data", params={"period": "month"})
        assert client_holder["c"]._last_params == {"period": "month"}

    @pytest.mark.asyncio
    async def test_404_raises_http_exception(self, monkeypatch):
        _patch_httpx(monkeypatch, response=_FakeResponse(404))
        with pytest.raises(HTTPException) as exc:
            await _proxy_get("http://sidecar/missing")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_connect_error_raises_503(self, monkeypatch):
        _patch_httpx(monkeypatch, error=_ConnectError("connection refused"))
        with pytest.raises(HTTPException) as exc:
            await _proxy_get("http://sidecar/down")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_timeout_raises_504(self, monkeypatch):
        _patch_httpx(monkeypatch, error=_TimeoutException("read timeout"))
        with pytest.raises(HTTPException) as exc:
            await _proxy_get("http://sidecar/slow")
        assert exc.value.status_code == 504

    @pytest.mark.asyncio
    async def test_generic_exception_raises_502(self, monkeypatch):
        _patch_httpx(monkeypatch, error=RuntimeError("something broke"))
        with pytest.raises(HTTPException) as exc:
            await _proxy_get("http://sidecar/error")
        assert exc.value.status_code == 502


# ── Router setup verification ──────────────────────────────────────────

class TestSetupNexusRoutes:
    """Ensure setup_nexus_routes registers every documented endpoint."""

    def _paths(self):
        return {r.path for r in setup_nexus_routes().routes}

    def test_cost_routes_registered(self):
        paths = self._paths()
        for p in [
            "/api/nexus/cost/health",
            "/api/nexus/cost/summary",
            "/api/nexus/cost/history",
            "/api/nexus/cost/by-model",
            "/api/nexus/cost/by-service",
            "/api/nexus/cost/trend",
            "/api/nexus/cost/budget",
            "/api/nexus/cost/budget/{service}",
            "/api/nexus/cost/alerts",
            "/api/nexus/cost/models",
        ]:
            assert p in paths, f"missing {p}"

    def test_metrics_routes_registered(self):
        paths = self._paths()
        for p in [
            "/api/nexus/metrics/health",
            "/api/nexus/metrics/all",
            "/api/nexus/metrics/{metric_type}",
        ]:
            assert p in paths, f"missing {p}"

    def test_news_routes_registered(self):
        paths = self._paths()
        for p in [
            "/api/nexus/news/health",
            "/api/nexus/news",
            "/api/nexus/news/search",
            "/api/nexus/news/category/{category}",
            "/api/nexus/news/digest",
            "/api/nexus/news/feeds",
            "/api/nexus/news/stats",
        ]:
            assert p in paths, f"missing {p}"

    def test_router_tag_is_nexus(self):
        router = setup_nexus_routes()
        for route in router.routes:
            assert "nexus" in getattr(route, "tags", [])
