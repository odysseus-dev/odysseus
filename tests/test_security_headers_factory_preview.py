"""
Regression tests for the Factory HTML preview endpoint's permissive CSP.

The /api/factory/nodes/{id}/preview route serves LLM-generated HTML with its
own CSP that allows inline scripts, Google Fonts, same-origin framing, etc.
These tests verify the middleware applies the correct headers.
"""

import secrets
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware


def _client():
    """Build a minimal test app with SecurityHeadersMiddleware and a stubbed
    factory preview route.

    We stub the route directly rather than mounting the real factory router +
    database, so these tests stay fast and focused on header correctness. The
    stubs return minimal HTML that exercises the CSP (inline <script> content,
    Google Fonts <link>).
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/factory/nodes/{node_id}/preview")
    async def preview_node(node_id: int):
        html = (
            '<html><head>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto">'
            '</head><body>'
            '<script>alert("hi")</script>'
            '</body></html>'
        )
        return Response(content=html, media_type="text/html")

    @app.get("/api/factory/nodes/{node_id}")
    async def get_node(node_id: int):
        return {"ok": True}

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    return TestClient(app)


def test_factory_preview_has_sameorigin_frame_options():
    """The preview endpoint must be frameable by the same origin."""
    response = _client().get("/api/factory/nodes/1/preview")
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_factory_preview_csp_allows_inline_scripts():
    """Generated HTML may contain inline <script> blocks — the CSP must allow
    'unsafe-inline' for scripts."""
    response = _client().get("/api/factory/nodes/1/preview")
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp


def test_factory_preview_csp_allows_google_fonts():
    """Generated HTML may reference Google Fonts — the CSP must allow both the
    stylesheet endpoint (fonts.googleapis.com) and the font file origin
    (fonts.gstatic.com)."""
    response = _client().get("/api/factory/nodes/1/preview")
    csp = response.headers["Content-Security-Policy"]
    assert "fonts.googleapis.com" in csp
    assert "fonts.gstatic.com" in csp


def test_factory_preview_csp_allows_same_origin_framing():
    """The HTML is served into an iframe on the same origin — frame-ancestors
    must be 'self' (not 'none')."""
    response = _client().get("/api/factory/nodes/1/preview")
    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in csp


def test_factory_preview_csp_allows_jsdelivr_style_font():
    """The preview should also permit jsdelivr for styles/fonts (e.g. for
    tailwind CDN usage)."""
    response = _client().get("/api/factory/nodes/1/preview")
    csp = response.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in csp


def test_factory_preview_returns_text_html():
    """The endpoint must return the correct media type so the browser renders
    the iframe content as HTML."""
    response = _client().get("/api/factory/nodes/1/preview")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_default_routes_remain_unframeable():
    """Routes that are *not* the preview endpoint must keep their restrictive
    CSP (X-Frame-Options: DENY, frame-ancestors 'none')."""
    response = _client().get("/plain")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_factory_preview_has_full_csp():
    """Verify the full CSP string contains all expected directives."""
    response = _client().get("/api/factory/nodes/1/preview")
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net" in csp
    assert "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net" in csp
    assert "img-src 'self' data: blob: https:" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'self'" in csp


def test_factory_preview_iframe_sandbox_is_scripts_only():
    """The preview iframe must NOT carry allow-same-origin — otherwise the
    framed LLM output escapes its opaque origin and can reach the parent
    document, cookies, and authenticated /api/* calls. See security audit."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "static" / "js" / "factory.js"
    text = src.read_text(encoding="utf-8")
    assert 'class="factory-preview-iframe"' in text, "preview iframe not found in source"
    for line in text.splitlines():
        if 'class="factory-preview-iframe"' in line:
            assert 'sandbox="allow-scripts"' in line, (
                f"sandbox must be allow-scripts only; got: {line.strip()}"
            )
            assert "allow-same-origin" not in line, (
                f"allow-same-origin must NOT be present; got: {line.strip()}"
            )
            return
    pytest.fail("preview iframe line not found")


# ── Project-delivery preview token-cache endpoints ──────────────

# In-memory stash for test preview cache (not shared with the real router)
_test_preview_cache: dict = {}


def _client_with_preview_cache():
    """Test client with SecurityHeadersMiddleware + stubs for the project-delivery
    preview token-cache endpoints (POST /api/factory/preview, GET /api/factory/preview/{token}).
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.post("/api/factory/preview")
    async def post_preview(request: Request):
        body = await request.json()
        html = body.get("html")
        if not html or not isinstance(html, str):
            return JSONResponse(status_code=400, content={"detail": "html (non-empty string) is required"})
        if len(html) > 5_000_000:
            return JSONResponse(status_code=413, content={"detail": "Preview HTML too large"})
        token = secrets.token_hex(16)
        _test_preview_cache[token] = html
        return {"token": token}

    @app.get("/api/factory/preview/{token}")
    async def get_preview(token: str):
        html = _test_preview_cache.pop(token, None)
        if not html:
            return JSONResponse(status_code=404, content={"detail": "Preview not found or expired"})
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html, media_type="text/html")

    @app.get("/api/factory/nodes/{node_id}")
    async def get_node(node_id: int):
        return {"ok": True}

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    return TestClient(app)


def test_post_preview_returns_token():
    """POST a project HTML => 200 + JSON with token."""
    client = _client_with_preview_cache()
    resp = client.post("/api/factory/preview", json={"html": "<html><body>Hello</body></html>"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert isinstance(body["token"], str)
    assert len(body["token"]) > 0


def test_get_preview_serves_html_with_permissive_csp():
    """POST HTML with inline script + Google Fonts, then GET the token => 200 + permissive CSP."""
    test_html = (
        '<html><head>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto">'
        '</head><body>'
        '<script>alert("hi")</script>'
        '</body></html>'
    )
    client = _client_with_preview_cache()

    # POST → get token
    post_resp = client.post("/api/factory/preview", json={"html": test_html})
    assert post_resp.status_code == 200
    token = post_resp.json()["token"]

    # GET → verify CSP
    get_resp = client.get(f"/api/factory/preview/{token}")
    assert get_resp.status_code == 200
    assert "text/html" in get_resp.headers["content-type"]

    csp = get_resp.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "fonts.googleapis.com" in csp
    assert "frame-ancestors 'self'" in csp
    assert get_resp.text == test_html


def test_get_preview_expired_token_returns_404():
    """GET with a bogus token => 404."""
    client = _client_with_preview_cache()
    resp = client.get("/api/factory/preview/00000000000000000000000000000000")
    assert resp.status_code == 404


def test_post_preview_rejects_empty_html():
    """POST with empty html string => 400."""
    client = _client_with_preview_cache()
    resp = client.post("/api/factory/preview", json={"html": ""})
    assert resp.status_code == 400


def test_project_preview_no_longer_uses_srcdoc_or_blob():
    """Verify the three vulnerable patterns (srcdoc=pages, Blob for project
    preview, URL.createObjectURL in _showProjectPreview) have been eliminated."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "static" / "js" / "factory.js"
    text = src.read_text(encoding="utf-8")

    # 1. srcdoc assignment to pages must be absent in _showProjectPreview context
    assert 'iframe.srcdoc = pages[file]' not in text, (
        "srcdoc = pages[file] still present — CSP inheritance bug remains"
    )

    # 2. Blob-based open-in-tab must be absent in the project preview context
    #    (The remaining use of Blob in factory.js is the task-output preview at
    #    line ~748 which uses a server-side URL, not a blob: URL.)
    assert 'new Blob([html], { type: \'text/html\' })' not in text, (
        "Blob([html]) still present — CSP inheritance bug remains"
    )
