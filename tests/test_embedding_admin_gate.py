"""Pin the admin gate on every /api/embeddings/* route.

These endpoints manage server-wide embedding state: they download/delete
cached models and repoint the global EMBEDDING_URL that every user's
RAG/semantic-memory traffic flows through. POST /endpoint additionally
fetches an arbitrary, caller-supplied URL (SSRF) before persisting it. The
router shipped with no auth, so any authenticated non-admin could hijack the
embedding backend for the whole instance. `require_admin` must gate all of
them.

The first test fails if a new endpoint is ever added to this router without
the gate — which is exactly how the original hole arose.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware import require_admin
from routes.embedding_routes import setup_embedding_routes


def _gate_calls(route):
    """Direct Depends(...) callables wired onto a route."""
    return [dep.call for dep in route.dependant.dependencies]


def test_every_embedding_route_requires_admin():
    router = setup_embedding_routes()
    http_routes = [r for r in router.routes if getattr(r, "dependant", None) is not None]
    assert http_routes, "no routes registered on the embeddings router"
    missing = [
        (r.path, sorted(r.methods))
        for r in http_routes
        if require_admin not in _gate_calls(r)
    ]
    assert not missing, f"embedding routes missing admin gate: {missing}"


class _FakeAuth:
    """Minimal stand-in for AuthManager: only "admin" is an admin."""

    is_configured = True

    def is_admin(self, username):
        return username == "admin"


def _client(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = FastAPI()

    # Mirror the real AuthMiddleware: stamp request.state.current_user so
    # require_admin can read it. Driven by a test header.
    @app.middleware("http")
    async def _attach_user(request, call_next):
        request.state.current_user = request.headers.get("X-Test-User") or None
        return await call_next(request)

    app.state.auth_manager = _FakeAuth()
    app.include_router(setup_embedding_routes())
    return TestClient(app)


def test_get_endpoint_blocks_non_admin_allows_admin(monkeypatch):
    client = _client(monkeypatch)
    # Authenticated non-admin is rejected...
    assert client.get("/api/embeddings/endpoint", headers={"X-Test-User": "alice"}).status_code == 403
    # ...anonymous too...
    assert client.get("/api/embeddings/endpoint").status_code == 403
    # ...but an admin passes the gate and gets the config back.
    resp = client.get("/api/embeddings/endpoint", headers={"X-Test-User": "admin"})
    assert resp.status_code == 200
    assert "url" in resp.json()


def test_set_endpoint_blocks_non_admin_before_ssrf(monkeypatch):
    import httpx

    def _must_not_call(*args, **kwargs):
        raise AssertionError("SSRF: httpx.post must not run for a non-admin")

    # If the gate were missing, set_endpoint would reach httpx.post(url) and
    # this would turn the AssertionError into a 500 instead of a clean 403.
    monkeypatch.setattr(httpx, "post", _must_not_call)

    client = _client(monkeypatch)
    resp = client.post(
        "/api/embeddings/endpoint",
        data={"url": "http://169.254.169.254/latest/meta-data/", "model": ""},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 403
