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

The tests AST-extract the security helpers — including the real
``_configure_security_middleware`` wiring — from diffusion_server.py and run
them against a fresh FastAPI app. That keeps the tests out of the torch /
diffusers import path while still exercising the production middleware wiring
instead of a hand-rebuilt copy.
"""

import ast
import importlib.util
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "diffusion_server.py"


_EXPECTED_NAMES = (
    "_DEFAULT_ALLOWED_HOSTS",
    "_DEFAULT_CORS_ORIGINS",
    "_compute_allowed_hosts",
    "_compute_cors_origins",
    "_configure_security_middleware",
)


def _load_helpers():
    """Extract the security helpers from diffusion_server.py via AST so the
    tests exercise the production wiring without importing the module (which
    would pull in torch / diffusers). Only the named top-level definitions are
    compiled into a fresh module; everything else — including the heavy
    imports — is left out. A renamed or removed helper fails loudly here."""
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    wanted: dict = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _EXPECTED_NAMES:
            wanted[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _EXPECTED_NAMES:
                    wanted[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in _EXPECTED_NAMES:
                wanted[node.target.id] = node

    missing = [name for name in _EXPECTED_NAMES if name not in wanted]
    assert not missing, f"diffusion_server.py is missing expected helpers: {missing}"

    module = ast.Module(body=[wanted[name] for name in _EXPECTED_NAMES], type_ignores=[])
    ast.fix_missing_locations(module)
    ns: dict = {
        "TrustedHostMiddleware": TrustedHostMiddleware,
        "CORSMiddleware": CORSMiddleware,
        "RuntimeError": RuntimeError,
        "list": list,
    }
    exec(compile(module, str(_SCRIPT), "exec"), ns)
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


def test_compute_allowed_hosts_preserves_explicit_wildcard():
    # Behavior preservation: a wildcard is not added by default, but an
    # operator who explicitly passes one is taken at their word (deduped,
    # stripped, stable order). This pins current behavior, not policy.
    ns = _load_helpers()
    out = ns["_compute_allowed_hosts"]("127.0.0.1", extras=["*", " lan.example ", "*"])
    assert out == ["127.0.0.1", "localhost", "::1", "*", "lan.example"]


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


def test_compute_cors_origins_preserves_explicit_wildcard():
    # Behavior preservation: a wildcard is not the default, but an operator
    # who explicitly passes one is taken at their word (deduped, stripped,
    # stable order). This pins current behavior, not policy.
    ns = _load_helpers()
    out = ns["_compute_cors_origins"](extras=["*", " http://localhost:7000 ", "*"])
    assert out == ["*", "http://localhost:7000"]


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


def _configured_app(ns, allowed_origins, route_called=None):
    """Fresh FastAPI app wired by the production `_configure_security_middleware`
    with a loopback Host allowlist, plus a minimal route so accepted requests
    can assert 200. If `route_called` is given, the route sets
    ``route_called["hit"] = True`` so callers can prove whether the inner app
    was reached."""
    from fastapi import FastAPI

    app = FastAPI()
    ns["_configure_security_middleware"](
        app, ns["_compute_allowed_hosts"]("127.0.0.1"), allowed_origins
    )

    @app.get("/")
    def root():
        if route_called is not None:
            route_called["hit"] = True
        return {"ok": True}

    return app


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_trusted_host_middleware_rejects_attacker_host():
    """A request with an attacker-controlled Host header (the DNS-rebinding
    surface) must be rejected by the production wiring before any route runs."""
    ns = _load_helpers()
    route_called = {"hit": False}
    app = _configured_app(ns, [], route_called=route_called)

    # Legitimate request (Host: 127.0.0.1) reaches the route.
    ok = _asgi_get(app, "http://127.0.0.1/")
    assert ok.status_code == 200
    assert route_called["hit"] is True
    # Attacker-controlled hostname (DNS-rebinding scenario) is rejected before
    # the route runs.
    route_called["hit"] = False
    bad = _asgi_get(app, "http://evil.example.com/")
    assert bad.status_code == 400
    assert route_called["hit"] is False


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_cors_default_deny_does_not_emit_wildcard_acao():
    """Default-deny CORS (no --allowed-origin) must not advertise any
    Access-Control-Allow-Origin, so a browser blocks cross-origin readers."""
    ns = _load_helpers()
    cors_origins = ns["_compute_cors_origins"]()
    assert cors_origins == []

    app = _configured_app(ns, cors_origins)

    # Host is allowed, so the request itself succeeds — but the response must
    # carry no ACAO, so a real browser would block the attacker page from
    # reading the body.
    resp = _asgi_get(
        app, "http://127.0.0.1/", headers={"Origin": "https://evil.example.com"}
    )
    assert resp.status_code == 200
    acao = resp.headers.get("access-control-allow-origin")
    assert acao is None or acao == "", (
        f"unexpected ACAO header: {acao!r} — the regression was wildcard CORS, "
        f"so any non-empty default fails this gate"
    )


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_explicit_cors_origin_does_not_widen_to_wildcard():
    """Even when the operator opts in to one cross-origin, that single origin
    must not unlock a wildcard reflection for other origins."""
    ns = _load_helpers()
    cors_origins = ns["_compute_cors_origins"](extras=["http://localhost:7000"])

    app = _configured_app(ns, cors_origins)

    # Allowed origin: ACAO echoes that origin (NOT '*').
    ok = _asgi_get(
        app, "http://127.0.0.1/", headers={"Origin": "http://localhost:7000"}
    )
    assert ok.status_code == 200
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:7000"
    # Foreign origin: ACAO must NOT echo it, must NOT be '*'.
    bad = _asgi_get(
        app, "http://127.0.0.1/", headers={"Origin": "https://evil.example.com"}
    )
    bad_acao = bad.headers.get("access-control-allow-origin")
    assert bad_acao != "*"
    assert bad_acao != "https://evil.example.com"


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_configure_security_middleware_preserves_order():
    """CORS is added last so it wraps TrustedHost (outermost). The production
    order must be user_middleware == [CORSMiddleware, TrustedHostMiddleware];
    default-deny installs the Host allowlist alone."""
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    ns = _load_helpers()

    with_cors = _configured_app(ns, ns["_compute_cors_origins"](extras=["http://localhost:7000"]))
    assert [m.cls for m in with_cors.user_middleware] == [CORSMiddleware, TrustedHostMiddleware]

    default_deny = _configured_app(ns, [])
    assert [m.cls for m in default_deny.user_middleware] == [TrustedHostMiddleware]


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_configure_security_middleware_is_idempotent_before_serving():
    """Re-running configuration (module-load defaults, then CLI override)
    replaces the stack rather than accumulating duplicate middleware."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    ns = _load_helpers()
    allowed = ns["_compute_allowed_hosts"]("127.0.0.1")

    app = FastAPI()
    ns["_configure_security_middleware"](app, allowed, [])
    ns["_configure_security_middleware"](
        app, allowed, ns["_compute_cors_origins"](extras=["http://localhost:7000"])
    )

    classes = [m.cls for m in app.user_middleware]
    assert classes == [CORSMiddleware, TrustedHostMiddleware]
    assert classes.count(TrustedHostMiddleware) == 1


@pytest.mark.skipif(not _starlette_available(), reason="starlette not installed")
def test_configure_security_middleware_rejects_late_call():
    """Once the middleware stack is built, the helper must raise before
    mutating user_middleware so a late reconfigure can't silently no-op."""
    from fastapi import FastAPI

    ns = _load_helpers()
    allowed = ns["_compute_allowed_hosts"]("127.0.0.1")

    app = FastAPI()
    ns["_configure_security_middleware"](app, allowed, [])
    before = list(app.user_middleware)

    # Simulate the app having started serving (stack built lazily on first req).
    app.middleware_stack = app.build_middleware_stack()
    assert app.middleware_stack is not None

    with pytest.raises(RuntimeError):
        ns["_configure_security_middleware"](app, ["lan.example"], [])
    # Guard fired before mutating: user_middleware is untouched.
    assert list(app.user_middleware) == before
