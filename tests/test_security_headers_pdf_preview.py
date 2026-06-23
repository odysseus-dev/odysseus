from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    @app.get("/api/document/{doc_id}/render-pdf")
    async def render_pdf(doc_id: str):
        return Response(b"%PDF-1.4\n", media_type="application/pdf")

    @app.get("/v2/gallery-editor-frame")
    async def gallery_editor_frame():
        return Response("<html></html>", media_type="text/html")

    return TestClient(app)


def test_default_routes_remain_unframeable():
    response = _client().get("/plain")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_default_routes_allow_data_uri_fonts():
    # The v2 bundle inlines a small woff2 as a base64 data-URI, so the main-app
    # CSP must permit `data:` in font-src or the browser logs a violation on
    # every route. Regression guard for that fix.
    response = _client().get("/plain")

    csp = response.headers["Content-Security-Policy"]
    assert "font-src 'self' data: https://cdn.jsdelivr.net" in csp


def test_document_pdf_preview_can_be_framed_by_same_origin():
    response = _client().get("/api/document/doc-123/render-pdf")

    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'self'"
    )


def test_gallery_editor_frame_can_be_framed_by_same_origin():
    response = _client().get("/v2/gallery-editor-frame")

    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "font-src 'self' data:" in csp
    assert "frame-ancestors 'self'" in csp
