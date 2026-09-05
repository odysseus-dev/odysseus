"""`SecurityHeadersMiddleware` must classify routes by application path.

`core/middleware.py` already documents the rule, on the helper it exports:

    Uvicorn prefixes ``scope["path"]`` with a configured ASGI ``root_path``;
    Starlette removes that prefix before matching routes. Middleware policy
    must use the same path form or a deployment prefix can change which policy
    applies to an otherwise unchanged application route.

`AuthMiddleware` follows it (see tests/test_auth_root_path.py).
`SecurityHeadersMiddleware` read `request.url.path`, which still carries the
prefix — so behind a reverse proxy mount (`--root-path /odysseus`, the
deployment SECURITY.md recommends) its three special-cased routes silently fell
through to the generic policy:

  * `/api/research/report/*` needs `script-src 'unsafe-inline'` — self-contained
    report HTML.
  * `/api/tools/*/render` needs framing headers omitted — it is iframed.
  * `/api/document/*/render-pdf` needs `SAMEORIGIN` / `frame-ancestors 'self'`.

Each is a same-origin feature that stops rendering under a mount path, with no
error to point at the cause. These tests pin the classification for both the
unmounted and mounted forms of the same application route.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware

_STATIC_DIR = Path(tempfile.mkdtemp(prefix="odysseus-static-"))


ROUTES = (
    "/api/research/report/abc",
    "/api/tools/mytool/render",
    "/api/document/doc1/render-pdf",
    "/api/other",
    # An ordinary route whose own name ends in a relaxed suffix. Used by the
    # collision cases: prefixed, its raw URL is indistinguishable from a tool
    # render route, while the route Starlette matches is this one.
    "/render",
)


def _client(root_path="", mount_static=False):
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    for route in ROUTES:
        app.add_api_route(
            route,
            lambda route=route: HTMLResponse(f"<html>{route}</html>"),
            methods=["GET"],
        )

    if mount_static:
        # Mirrors app.py: app.mount("/static", ...). A Mount rewrites the same
        # scope dict it matches on, which is what makes classification order
        # observable.
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Mirror what uvicorn's --root-path does: the ASGI scope keeps the prefixed
    # path, and root_path tells Starlette how much of it to strip before route
    # matching. TestClient(root_path=...) sets exactly that.
    return TestClient(app, root_path=root_path)


def _get(route, root_path="", mount_static=False):
    return _client(root_path, mount_static).get(root_path + route)


def _headers(route, root_path=""):
    return _get(route, root_path).headers


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_report_route_keeps_its_inline_script_policy(root_path):
    csp = _headers("/api/research/report/abc", root_path)["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "nonce-" not in csp


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_tool_render_route_keeps_framing_headers_omitted(root_path):
    headers = _headers("/api/tools/mytool/render", root_path)
    assert "x-frame-options" not in headers
    assert "content-security-policy" not in headers


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_pdf_preview_route_keeps_same_origin_framing(root_path):
    headers = _headers("/api/document/doc1/render-pdf", root_path)
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in headers["content-security-policy"]


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_ordinary_route_keeps_the_strict_default_policy(root_path):
    headers = _headers("/api/other", root_path)
    assert headers["x-frame-options"] == "DENY"
    csp = headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "nonce-" in csp


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_baseline_headers_are_unconditional(root_path):
    # These must not depend on which branch the route classifies into.
    for route in ROUTES:
        headers = _headers(route, root_path)
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert "camera=()" in headers["permissions-policy"]


def test_a_mount_prefix_cannot_be_spelled_to_claim_a_relaxed_policy():
    """A route is classified by what Starlette routes, not by the raw URL.

    The prefix is chosen so the *raw* URL ends in `/render` under a
    `/api/tools/...` prefix — indistinguishable from a tool render route to a
    classifier reading `request.url.path` — while the route Starlette matches
    is the ordinary `/render`. Asserting the body as well as the headers is
    what makes this a real collision test: without it the case would still pass
    if the request had 404'd and never reached the route at all.
    """
    response = _get("/render", "/api/tools/x")

    assert response.status_code == 200
    assert response.text == "<html>/render</html>"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    "prefix, route",
    [
        ("/api/tools/x", "/render"),
        ("/api/document/d", "/render-pdf"),
        ("/api/research", "/report/abc"),
    ],
)
def test_no_relaxed_prefix_spelling_survives_route_matching(prefix, route):
    """Same collision for each of the three relaxed branches."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_api_route(route, lambda: HTMLResponse("<html>ok</html>"), methods=["GET"])
    response = TestClient(app, root_path=prefix).get(prefix + route)

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_a_mounted_child_path_does_not_inherit_a_relaxed_policy():
    """A Mount rewrites the scope it matched, so classify before dispatching.

    Starlette moves the mount prefix into ``root_path`` and strips it from
    ``path`` on the same dict the middleware holds. Reading the path after
    ``call_next`` therefore sees `/api/tools/x/render` for a request that only
    ever reached the static mount, and hands that response the branch that
    omits `X-Frame-Options` and `Content-Security-Policy` entirely.
    """
    response = _get("/static/api/tools/x/render", mount_static=True)

    # The file does not exist: this is the static mount's 404, not a route.
    assert response.status_code == 404
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_a_mounted_child_path_keeps_the_unconditional_headers():
    response = _get("/static/api/document/d/render-pdf", mount_static=True)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
