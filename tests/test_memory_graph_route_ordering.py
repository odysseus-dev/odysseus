"""Regression guard for the one real collision risk in this feature.

routes/memory/memory_routes.py registers `GET/PUT/DELETE /api/memory/{memory_id}`
as a single-segment wildcard. Starlette matches routes in registration order
across the whole app, not by specificity, so `GET /api/memory/graph` would be
silently swallowed by that wildcard (memory_id="graph") if memory_router were
ever included before memory_graph_router. app.py documents and enforces the
required order; this test builds a minimal app the same way and proves a real
HTTP request resolves to the graph handler, not the wildcard 404 path — a
plain "call the endpoint function directly" test (the repo's usual route-test
style) can't catch this class of bug because it looks up routes by exact
path-string equality, not by simulating Starlette's request matching.
"""
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from routes.memory.memory_graph_routes import setup_memory_graph_routes
from routes.memory.memory_routes import setup_memory_routes


def _build_app():
    app = FastAPI()

    memory_manager = MagicMock()
    memory_manager.load.return_value = []
    session_manager = MagicMock()

    # Mirrors app.py: memory_graph_router included BEFORE memory_router.
    app.include_router(setup_memory_graph_routes(memory_manager, memory_vector=None))
    app.include_router(setup_memory_routes(memory_manager, session_manager, memory_vector=None))

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.current_user = "alice"
        request.state.api_token = False
        return await call_next(request)

    return app


def test_graph_route_is_not_swallowed_by_memory_id_wildcard():
    client = TestClient(_build_app())
    resp = client.get("/api/memory/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body and "edges" in body


def test_memory_id_wildcard_still_works_for_real_ids():
    client = TestClient(_build_app())
    resp = client.get("/api/memory/some-real-id")
    # Not found (empty memory store), but resolved by the wildcard handler,
    # not a 422/other error — proves the wildcard route still works normally
    # once the graph route (checked first) doesn't match.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Memory not found"
