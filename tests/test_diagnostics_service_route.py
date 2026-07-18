"""Route-level regression tests for admin diagnostics endpoints.

The reviewer asked for explicit coverage of unauthenticated / non-admin / admin
access to this admin diagnostics route, beyond the unit tests for the collector.

These need a real FastAPI + TestClient (the conftest only stubs FastAPI when it
is *not* installed). When the full app deps aren't present we skip rather than
fail, so the suite stays green in minimal environments; CI installs
requirements, so the tests run there.
"""
import asyncio
import threading

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("starlette.testclient")

from fastapi import FastAPI, HTTPException, Request
from starlette.testclient import TestClient

# Importing the route module pulls a few app deps; skip cleanly if unavailable.
diag = pytest.importorskip("routes.diagnostics_routes")


def _client_with_admin_gate(monkeypatch, gate):
    """Mount the diagnostics router with `require_admin` and the collector
    patched (via monkeypatch so the module globals are restored afterwards),
    and return a TestClient. `gate` plays the role of require_admin."""
    import src.service_health as sh

    async def _fake_collect(_rag, _mem):
        return {"overall": "ok", "services": [], "timestamp": "t"}

    # monkeypatch.setattr restores these after the test — a plain assignment
    # would leak the fakes into every later test in the session.
    monkeypatch.setattr(diag, "require_admin", gate)
    monkeypatch.setattr(sh, "collect_service_health", _fake_collect)

    app = FastAPI()
    app.include_router(diag.setup_diagnostics_routes(
        rag_manager=None, rag_available=False, research_handler=None,
        memory_vector=None))
    return TestClient(app, raise_server_exceptions=False)


def test_unauthenticated_is_rejected(monkeypatch):
    def gate(_request: Request):
        raise HTTPException(401, "Not authenticated")
    client = _client_with_admin_gate(monkeypatch, gate)
    r = client.get("/api/diagnostics/services")
    assert r.status_code == 401


def test_non_admin_is_forbidden(monkeypatch):
    def gate(_request: Request):
        raise HTTPException(403, "Admin only")
    client = _client_with_admin_gate(monkeypatch, gate)
    r = client.get("/api/diagnostics/services")
    assert r.status_code == 403


def test_admin_gets_report(monkeypatch):
    def gate(_request: Request):
        return None  # admin allowed
    client = _client_with_admin_gate(monkeypatch, gate)
    r = client.get("/api/diagnostics/services")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"overall", "services", "timestamp"}
    assert body["overall"] == "ok"


def _storage_bloat_endpoint():
    router = diag.setup_diagnostics_routes(
        rag_manager=None,
        rag_available=False,
        research_handler=None,
        memory_vector=None,
    )
    return next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/diagnostics/storage-bloat"
    )


@pytest.mark.asyncio
async def test_storage_bloat_collection_leaves_event_loop_responsive(monkeypatch):
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    collector_started = asyncio.Event()
    collector_release = threading.Event()
    collector_threads = []
    expected = {
        "status": "success",
        "database": {},
        "uploads": {},
        "warnings": [],
    }

    def blocked_collector(*, default_db_path, upload_dir):
        assert default_db_path == diag.APP_DB
        assert upload_dir == diag.UPLOAD_DIR
        collector_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(collector_started.set)
        if not collector_release.wait(timeout=2):
            raise AssertionError("collector was not released by the event loop")
        return expected

    monkeypatch.setattr(diag, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        diag,
        "collect_configured_storage_bloat_diagnostics",
        blocked_collector,
    )
    endpoint = _storage_bloat_endpoint()
    route_task = asyncio.create_task(endpoint(object()))

    try:
        await asyncio.wait_for(collector_started.wait(), timeout=2)
        heartbeat = asyncio.Event()
        loop.call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=2)

        assert route_task.done() is False
        assert len(collector_threads) == 1
        assert collector_threads[0] != event_loop_thread
    finally:
        collector_release.set()

    assert await asyncio.wait_for(route_task, timeout=2) == expected


def test_storage_bloat_collector_error_returns_generic_500(monkeypatch):
    def failing_collector(*, default_db_path, upload_dir):
        raise RuntimeError("private database path and credentials")

    monkeypatch.setattr(diag, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        diag,
        "collect_configured_storage_bloat_diagnostics",
        failing_collector,
    )
    app = FastAPI()
    app.include_router(
        diag.setup_diagnostics_routes(
            rag_manager=None,
            rag_available=False,
            research_handler=None,
            memory_vector=None,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/diagnostics/storage-bloat")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Failed to retrieve storage bloat diagnostics"
    }
    assert "private database path" not in response.text
    assert "credentials" not in response.text
