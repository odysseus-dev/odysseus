import importlib

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

import core.middleware as mw
from core.middleware import SecurityHeadersMiddleware, clawagent_frame_origin


def _client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    @app.get("/clawagent")
    async def clawagent():
        return Response(b"<html></html>", media_type="text/html")

    return TestClient(app)


def test_origin_helper_extracts_scheme_and_host(monkeypatch):
    monkeypatch.setenv("CLAWAGENT_URL", "https://clawagent.up.railway.app/x?y=1")
    assert clawagent_frame_origin() == "https://clawagent.up.railway.app"

    monkeypatch.setenv("CLAWAGENT_URL", "http://host:8080")
    assert clawagent_frame_origin() == "http://host:8080"


def test_origin_helper_rejects_non_http_and_empty(monkeypatch):
    monkeypatch.setenv("CLAWAGENT_URL", "ftp://nope")
    assert clawagent_frame_origin() == ""

    monkeypatch.setenv("CLAWAGENT_URL", "javascript:alert(1)")
    assert clawagent_frame_origin() == ""

    monkeypatch.delenv("CLAWAGENT_URL", raising=False)
    assert clawagent_frame_origin() == ""


def test_clawagent_route_allows_configured_origin_in_frame_src(monkeypatch):
    monkeypatch.setenv("CLAWAGENT_URL", "https://clawagent.up.railway.app")
    response = _client().get("/clawagent")

    csp = response.headers["Content-Security-Policy"]
    assert "frame-src 'self' https://clawagent.up.railway.app;" in csp
    # The rest of the app is unaffected — only this route is relaxed.
    assert "frame-ancestors 'none'" in csp


def test_clawagent_route_stays_strict_when_unset(monkeypatch):
    monkeypatch.delenv("CLAWAGENT_URL", raising=False)
    response = _client().get("/clawagent")

    csp = response.headers["Content-Security-Policy"]
    assert "frame-src 'self';" in csp
    assert "clawagent" not in csp


def test_other_routes_keep_default_frame_policy(monkeypatch):
    monkeypatch.setenv("CLAWAGENT_URL", "https://clawagent.up.railway.app")
    response = _client().get("/plain")

    assert response.headers["X-Frame-Options"] == "DENY"
    csp = response.headers["Content-Security-Policy"]
    assert "frame-src 'self';" in csp
    assert "clawagent" not in csp
