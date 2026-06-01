"""Pin the diffusion_server DNS-rebinding + wildcard-CORS regression.

Background: scripts/diffusion_server.py used to ship `allow_origins=["*"]`
with the default `--host=127.0.0.1` bind. Combined, that left the OpenAI-
compatible image API reachable from any browser tab via DNS-rebinding: an
attacker page resolves its own domain to 127.0.0.1 mid-fetch, the browser
forwards the request to the loopback server, and the wildcard CORS reply
lets the attacker page read the result + drive the GPU.

The fix narrows CORS to default-deny and adds a TrustedHostMiddleware
Host-header allowlist as a positive defense. These tests pin the allowlist
helpers + Starlette's middleware behavior so a future change can't silently
re-open the hole.

The tests run against a tiny synthetic FastAPI app that uses the same
``TrustedHostMiddleware`` + ``CORSMiddleware`` wiring as diffusion_server.
That keeps the test out of the torch / diffusers import path while still
covering the live middleware code paths.
"""

import importlib.util
import os
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "diffusion_server.py"


def _load_helpers():
    """Import the pure allowlist helpers from diffusion_server.py without
    triggering its torch / diffusers imports. We compile just the helper
    block (everything between the `app =` line and the `class ImageRequest`
    line) so heavy deps stay quarantined behind the if-False import guard.
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    # The helpers live between the two markers, both inserted by the security
    # fix. They depend only on the `_DEFAULT_ALLOWED_HOSTS` / `_DEFAULT_CORS_ORIGINS`
    # module-level lists, which we materialise here.
    start_marker = "_DEFAULT_ALLOWED_HOSTS = "
    end_marker = "class ImageRequest("
    i = src.index(start_marker)
    j = src.index(end_marker)
    helper_block = src[i:j]
    ns: dict = {"list": list}
    # Strip the `app.add_middleware(...)` line — the helpers don't need it
    # and it would force a torch import via fastapi.responses.
    helper_block = "\n".join(
        line for line in helper_block.splitlines()
        if not line.startswith("app.add_middleware")
    )
    exec(compile(helper_block, str(_SCRIPT), "exec"), ns)
    return ns


def test_compute_allowed_hosts_includes_loopback_and_bind_host():
    ns = _load_helpers()
    out = ns["_compute_allowed_hosts"]("0.0.0.0")
    assert "0.0.0.0" in out
    assert "127.0.0.1" in out
    assert "localhost" in out
    assert "::1" in out


def test_compute_allowed_hosts_dedupes_and_strips():
    ns = _load_helpers()
    # Bind host duplicates a default + an extra duplicates a default + blanks
    # all collapse into one entry per unique value, preserving stable order.
    out = ns["_compute_allowed_hosts"]("127.0.0.1", extras=["localhost", "", "  ", "lan.example"])
    assert out == ["127.0.0.1", "localhost", "::1", "lan.example"]


def test_compute_allowed_hosts_does_not_add_wildcard():
    ns = _load_helpers()
    out = ns["_compute_allowed_hosts"]("127.0.0.1")
    assert "*" not in out, "wildcard host would re-open the DNS-rebinding hole"


def test_compute_cors_origins_default_deny():
    ns = _load_helpers()
    out = ns["_compute_cors_origins"]()
    assert out == [], "default CORS allowlist must be empty (no cross-origin)"


def test_compute_cors_origins_does_not_default_to_wildcard():
    """Regression: the original code shipped allow_origins=['*']. The fix
    must NOT bring that back even when the operator passes nothing."""
    ns = _load_helpers()
    out = ns["_compute_cors_origins"](extras=None)
    assert "*" not in out
    out2 = ns["_compute_cors_origins"](extras=[])
    assert "*" not in out2


def test_compute_cors_origins_honours_explicit_extras():
    ns = _load_helpers()
    out = ns["_compute_cors_origins"](extras=["http://localhost:7000", "", "http://localhost:7000"])
    assert out == ["http://localhost:7000"]


# ── Live middleware integration: TrustedHostMiddleware + CORSMiddleware ─────


def _starlette_available() -> bool:
    return importlib.util.find_spec("starlette") is not None


def _asgi_get(app, url, headers=None):
    """Drive a single GET against an ASGI ``app`` over httpx's in-process
    ``ASGITransport`` on a fresh event loop.

    This deliberately avoids ``starlette.testclient.TestClient``: its
    context-manager form spins up an ``anyio`` blocking portal (to run the
    lifespan), which deadlocks under some pytest / anyio / asyncio test
    configurations — the focused Host-header test hung indefinitely during
    review (see PR #347). A direct ASGI call needs neither a portal nor a
    lifespan, so it stays reliable regardless of the host project's async
    test plugins.

    The request ``Host`` is derived from ``url`` so the TrustedHost allowlist
    sees exactly the hostname under test; ``Origin`` and friends go through
    ``headers``.
    """
    import asyncio

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get(url, headers=headers or {})

    return asyncio.run(_run())


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_trusted_host_middleware_rejects_attacker_host():
    """A request with an attacker-controlled Host header (the DNS-rebinding
    surface) must be rejected by the middleware before reaching any route."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware  # noqa: F401  (parity import)
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    ns = _load_helpers()
    allowed = ns["_compute_allowed_hosts"]("127.0.0.1")

    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Legitimate request (Host: 127.0.0.1) goes through.
    ok = _asgi_get(app, "http://127.0.0.1/health")
    assert ok.status_code == 200
    # Attacker-controlled hostname (DNS-rebinding scenario) is rejected.
    bad = _asgi_get(app, "http://evil.example.com/health")
    assert bad.status_code == 400


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_cors_default_deny_does_not_emit_wildcard_acao():
    """Without CORSMiddleware installed, the server must not advertise
    Access-Control-Allow-Origin at all (definitely not the wildcard)."""
    from fastapi import FastAPI
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    ns = _load_helpers()
    allowed = ns["_compute_allowed_hosts"]("127.0.0.1")
    # Default-deny CORS: no CORSMiddleware. Mirrors diffusion_server's behavior
    # when no --allowed-origin flags are passed.
    cors_origins = ns["_compute_cors_origins"]()
    assert cors_origins == []

    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)

    @app.get("/v1/models")
    def list_models():
        return {"data": []}

    # Host is allowed, so the request itself succeeds — but the response must
    # carry no ACAO, so a real browser would block the attacker page from
    # reading the body.
    resp = _asgi_get(
        app,
        "http://127.0.0.1/v1/models",
        headers={"Origin": "https://evil.example.com"},
    )
    acao = resp.headers.get("access-control-allow-origin")
    assert acao is None or acao == "", (
        f"unexpected ACAO header: {acao!r} — the regression was wildcard CORS, "
        f"so any non-empty default fails this gate"
    )


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_explicit_cors_origin_does_not_widen_to_wildcard():
    """Even when the operator opts in to one cross-origin, that single origin
    must not unlock a wildcard reflection for other origins."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    ns = _load_helpers()
    allowed = ns["_compute_allowed_hosts"]("127.0.0.1")
    cors_origins = ns["_compute_cors_origins"](extras=["http://localhost:7000"])

    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/v1/models")
    def list_models():
        return {"data": []}

    # Allowed origin: ACAO echoes that origin (NOT '*').
    ok = _asgi_get(
        app,
        "http://127.0.0.1/v1/models",
        headers={"Origin": "http://localhost:7000"},
    )
    assert ok.status_code == 200
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:7000"
    # Foreign origin: ACAO must NOT echo it, must NOT be '*'.
    bad = _asgi_get(
        app,
        "http://127.0.0.1/v1/models",
        headers={"Origin": "https://evil.example.com"},
    )
    bad_acao = bad.headers.get("access-control-allow-origin")
    assert bad_acao != "*"
    assert bad_acao != "https://evil.example.com"
