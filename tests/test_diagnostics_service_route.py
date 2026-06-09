"""Route-level regression tests for GET /api/diagnostics/services.

The reviewer asked for explicit coverage of unauthenticated / non-admin / admin
access to this admin diagnostics route, beyond the unit tests for the collector.

These need a real FastAPI + TestClient (the conftest only stubs FastAPI when it
is *not* installed). When the full app deps aren't present we skip rather than
fail, so the suite stays green in minimal environments; CI installs
requirements, so the tests run there.
"""
import pytest

fastapi = pytest.importorskip("fastapi")
starlette_testclient = pytest.importorskip("starlette.testclient")

from fastapi import FastAPI, HTTPException, Request
from starlette.testclient import TestClient

# Importing the route module pulls a few app deps; skip cleanly if unavailable.
diag = pytest.importorskip("routes.diagnostics_routes")


def _client_with_admin_gate(gate):
    """Mount the diagnostics router with `require_admin` and the collector
    patched, and return a TestClient. `gate` plays the role of require_admin."""
    app = FastAPI()
    # Patch the symbols the handler resolves at call time.
    diag.require_admin = gate

    async def _fake_collect(_rag, _mem):
        return {"overall": "ok", "services": [], "timestamp": "t"}

    import src.service_health as sh
    sh.collect_service_health = _fake_collect

    app.include_router(diag.setup_diagnostics_routes(
        rag_manager=None, rag_available=False, research_handler=None,
        memory_vector=None))
    return TestClient(app, raise_server_exceptions=False)


def test_unauthenticated_is_rejected():
    def gate(_request: Request):
        raise HTTPException(401, "Not authenticated")
    client = _client_with_admin_gate(gate)
    r = client.get("/api/diagnostics/services")
    assert r.status_code == 401


def test_non_admin_is_forbidden():
    def gate(_request: Request):
        raise HTTPException(403, "Admin only")
    client = _client_with_admin_gate(gate)
    r = client.get("/api/diagnostics/services")
    assert r.status_code == 403


def test_admin_gets_report():
    def gate(_request: Request):
        return None  # admin allowed
    client = _client_with_admin_gate(gate)
    r = client.get("/api/diagnostics/services")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"overall", "services", "timestamp"}
    assert body["overall"] == "ok"
