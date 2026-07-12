"""
Regression tests for the Factory HTML preview endpoint's permissive CSP.

The /api/factory/nodes/{id}/preview route serves LLM-generated HTML with its
own CSP that allows inline scripts, Google Fonts, same-origin framing, etc.
CSP is now set per-route (on the HTMLResponse) rather than in middleware,
so these tests verify the route handlers set the correct headers directly.
"""

import secrets
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware
from routes.factory_routes import factory_preview_middleware

# Shared CSP constant matching the one in routes/factory_routes.py
_FACTORY_PREVIEW_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob: https:; "
    "media-src 'self' blob: https:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'"
)


def _client():
    """Build a minimal test app with SecurityHeadersMiddleware + outer
    factory_preview_middleware and a stubbed factory preview route.

    The middleware stack matches app.py: SecurityHeadersMiddleware is inner,
    factory_preview_middleware is outer so it runs last in the response phase
    and can override headers for preview paths. We stub the route directly
    rather than mounting the real factory router + database, so these tests
    stay fast and focused on header correctness. The stubs return minimal HTML
    that exercises the CSP (inline <script> content, Google Fonts <link>).
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.middleware("http")(factory_preview_middleware)

    @app.get("/api/factory/nodes/{node_id}/preview")
    async def preview_node(node_id: int):
        html = (
            '<html><head>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto">'
            '</head><body>'
            '<script>alert("hi")</script>'
            '</body></html>'
        )
        from fastapi.responses import HTMLResponse
        response = HTMLResponse(content=html, media_type="text/html")
        return response

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
_test_preview_main: dict = {}


def _client_with_preview_cache():
    """Test client with SecurityHeadersMiddleware + outer factory_preview_middleware
    + stubs for the project-delivery preview token-cache endpoints (POST
    /api/factory/preview, GET /api/factory/preview/{token}).
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.middleware("http")(factory_preview_middleware)

    @app.post("/api/factory/preview")
    async def post_preview(request: Request):
        body = await request.json()
        files = body.get("files")
        main_file = body.get("main", "")
        if not files or not isinstance(files, dict) or not main_file:
            return JSONResponse(status_code=400, content={"detail": "files (dict) and main (filename) are required"})
        if len(main_file) > 500:
            return JSONResponse(status_code=400, content={"detail": "Invalid main filename"})
        token = secrets.token_hex(16)
        _test_preview_cache[token] = files
        _test_preview_main[token] = main_file
        return {"token": token}

    @app.get("/api/factory/preview/{token}")
    async def get_preview(token: str):
        from fastapi.responses import HTMLResponse
        files = _test_preview_cache.get(token)
        if not files:
            return JSONResponse(status_code=404, content={"detail": "Preview not found or expired"})
        main_file = _test_preview_main.get(token, "index.html")
        html = files.get(main_file) or files.get("index.html") or ""
        if not html:
            return JSONResponse(status_code=404, content={"detail": "No preview content"})
        response = HTMLResponse(content=html, media_type="text/html")
        return response

    @app.get("/api/factory/preview/{token}/{file_path:path}")
    async def get_preview_file(token: str, file_path: str):
        import mimetypes
        from fastapi.responses import Response
        files = _test_preview_cache.get(token)
        if not files:
            return JSONResponse(status_code=404, content={"detail": "Preview not found or expired"})
        content = files.get(file_path)
        if content is None:
            basename = file_path.rsplit("/", 1)[-1]
            content = files.get(basename)
        if content is None:
            return JSONResponse(status_code=404, content={"detail": f"File not found: {file_path}"})
        mimetype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        response = Response(content=content, media_type=mimetype)
        return response

    @app.get("/api/factory/nodes/{node_id}")
    async def get_node(node_id: int):
        return {"ok": True}

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    return TestClient(app)


def test_post_preview_returns_token():
    """POST project files => 200 + JSON with token."""
    client = _client_with_preview_cache()
    resp = client.post("/api/factory/preview", json={
        "files": {"index.html": "<html><body>Hello</body></html>"},
        "main": "index.html"
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert isinstance(body["token"], str)
    assert len(body["token"]) > 0


def test_get_preview_serves_html_with_permissive_csp():
    """POST files with inline script + Google Fonts, then GET the token => 200 + permissive CSP."""
    test_html = (
        '<html><head>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto">'
        '</head><body>'
        '<script>alert("hi")</script>'
        '</body></html>'
    )
    client = _client_with_preview_cache()

    # POST → get token
    post_resp = client.post("/api/factory/preview", json={
        "files": {"index.html": test_html},
        "main": "index.html"
    })
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


def test_post_preview_rejects_empty_body():
    """POST with missing files => 400."""
    client = _client_with_preview_cache()
    resp = client.post("/api/factory/preview", json={"files": {}, "main": ""})
    assert resp.status_code == 400


def test_get_preview_file_serves_js_with_correct_mime():
    """GET /preview/{token}/js/main.js => 200, JS content, correct MIME type."""
    client = _client_with_preview_cache()
    post_resp = client.post("/api/factory/preview", json={
        "files": {
            "index.html": "<html><script src='js/main.js'></script></html>",
            "js/main.js": "console.log(1);"
        },
        "main": "index.html"
    })
    assert post_resp.status_code == 200
    token = post_resp.json()["token"]

    resp = client.get(f"/api/factory/preview/{token}/js/main.js")
    assert resp.status_code == 200
    assert resp.text == "console.log(1);"
    ct = resp.headers["content-type"]
    is_js = "application/javascript" in ct or "text/javascript" in ct
    assert is_js, f"Expected JS MIME type, got: {ct}"
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp


def test_get_preview_file_returns_404_for_missing():
    """GET /preview/{token}/nonexistent.js => 404."""
    client = _client_with_preview_cache()
    post_resp = client.post("/api/factory/preview", json={
        "files": {"index.html": "<html></html>"},
        "main": "index.html"
    })
    assert post_resp.status_code == 200
    token = post_resp.json()["token"]

    resp = client.get(f"/api/factory/preview/{token}/nonexistent.js")
    assert resp.status_code == 404


def test_project_preview_uses_post_preview_files():
    """Verify that factory.js uses _postPreviewFiles instead of _postPreviewUrl,
    and that _inlineHTML has been removed."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "static" / "js" / "factory.js"
    text = src.read_text(encoding="utf-8")

    # Must use _postPreviewFiles now
    assert '_postPreviewFiles(files, mainFile)' in text or '_postPreviewFiles(files, activeFile)' in text, (
        "_postPreviewFiles call not found — the frontend must POST all files, not individual HTML"
    )
    # _inlineHTML must be removed
    assert '_inlineHTML' not in text, (
        "_inlineHTML still present — the frontend should not inline dependencies client-side"
    )
    # _postPreviewUrl must be removed
    assert '_postPreviewUrl' not in text, (
        "_postPreviewUrl still present — should be replaced by _postPreviewFiles"
    )
    # srcdoc assignment must be absent
    assert 'iframe.srcdoc' not in text, (
        "iframe.srcdoc still present — CSP inheritance bug remains"
    )
    # Blob-based open-in-tab must be absent
    assert 'new Blob([html], { type: \'text/html\' })' not in text, (
        "Blob([html]) still present — CSP inheritance bug remains"
    )
