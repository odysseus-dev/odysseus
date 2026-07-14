"""Tests for the operator core: envelope shape, health cache, degraded results, audit."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _fresh_cache():
    from services.operator.core import reset_status_cache
    reset_status_cache()
    yield
    reset_status_cache()


def test_envelope_shape():
    from services.operator.core import envelope

    result = envelope("screen_perception", True, data={"frames": []})
    assert result == {
        "ok": True,
        "capability": "screen_perception",
        "data": {"frames": []},
        "degraded": False,
    }


def test_degraded_envelope_includes_hint():
    from services.operator.core import degraded_envelope

    result = degraded_envelope("desktop_action", "clicky_offline")
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["reason"] == "clicky_offline"
    assert "clicky" in result["hint"].lower()


def test_capability_status_caches_within_ttl():
    from services.operator import core

    with patch.object(core, "_probe_screen_perception", return_value=(True, {"endpoint": "x"})) as probe:
        core._PROBES[core.CAP_SCREEN_PERCEPTION] = probe
        try:
            first = core.capability_status(core.CAP_SCREEN_PERCEPTION)
            second = core.capability_status(core.CAP_SCREEN_PERCEPTION)
        finally:
            core._PROBES[core.CAP_SCREEN_PERCEPTION] = core._probe_screen_perception
    assert first["available"] is True
    assert second is first
    assert probe.call_count == 1


def test_capability_status_force_reprobes():
    from services.operator import core

    with patch.object(core, "_probe_screen_perception", return_value=(True, {"endpoint": "x"})) as probe:
        core._PROBES[core.CAP_SCREEN_PERCEPTION] = probe
        try:
            core.capability_status(core.CAP_SCREEN_PERCEPTION)
            core.capability_status(core.CAP_SCREEN_PERCEPTION, force=True)
        finally:
            core._PROBES[core.CAP_SCREEN_PERCEPTION] = core._probe_screen_perception
    assert probe.call_count == 2


def test_require_capability_returns_none_when_available():
    from services.operator import core

    core._status_cache[core.CAP_RESEARCH] = (time.monotonic(), {"available": True})
    assert core.require_capability(core.CAP_RESEARCH) is None


def test_require_capability_degrades_when_offline():
    from services.operator import core

    core._status_cache[core.CAP_DESKTOP_ACTION] = (time.monotonic(), {"available": False})
    result = core.require_capability(core.CAP_DESKTOP_ACTION)
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["reason"] == "desktop_action_offline"
    assert result["hint"]


def test_get_operator_status_covers_all_capabilities():
    from services.operator import core

    offline = (False, {"endpoint": "down"})
    with patch.object(core, "_PROBES", {name: (lambda: offline) for name in core._PROBES}):
        status = core.get_operator_status(force=True)
    caps = status["capabilities"]
    assert set(caps) == {
        "screen_perception", "pixel_retrieval", "spec_tracer",
        "desktop_action", "browser_action", "research",
    }
    for entry in caps.values():
        assert entry["available"] is False
        assert "probed_at" in entry


def test_probe_exception_is_contained():
    from services.operator import core

    def boom():
        raise RuntimeError("sidecar exploded")

    with patch.object(core, "_PROBES", {**core._PROBES, core.CAP_BROWSER_ACTION: boom}):
        entry = core.capability_status(core.CAP_BROWSER_ACTION, force=True)
    assert entry["available"] is False
    assert "sidecar exploded" in entry.get("error", "")


def test_record_audit_writes_row():
    pytest.importorskip("sqlalchemy")
    from core.database import OperatorAudit, SessionLocal
    from services.operator.core import record_audit

    audit_id = record_audit(
        "browser_action", "navigate",
        target="https://example.com", session_id="sess-1", result="ok",
    )
    assert audit_id
    db = SessionLocal()
    try:
        row = db.query(OperatorAudit).filter(OperatorAudit.id == audit_id).one()
        assert row.capability == "browser_action"
        assert row.action == "navigate"
        assert row.target == "https://example.com"
        assert row.session_id == "sess-1"
        assert row.result == "ok"
    finally:
        db.close()


def test_record_audit_never_raises(monkeypatch):
    from services.operator import core

    def broken_import(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("core.database.SessionLocal", broken_import, raising=False)
    assert core.record_audit("desktop_action", "click") is None


# ── Route ──

def test_status_route_requires_auth():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    import routes.operator_routes as operator_routes

    def deny(_request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    with patch.object(operator_routes, "require_authenticated_request", deny):
        app = FastAPI()
        app.include_router(operator_routes.setup_operator_routes())
        client = TestClient(app)
        response = client.get("/api/operator/status")
    assert response.status_code == 401


def test_status_route_returns_snapshot():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routes.operator_routes as operator_routes

    snapshot = {"capabilities": {"research": {"available": True}}, "generated_at": 1.0}
    with patch.object(operator_routes, "require_authenticated_request", lambda _r: "tester"):
        with patch.object(operator_routes, "get_operator_status", lambda force=False: snapshot):
            app = FastAPI()
            app.include_router(operator_routes.setup_operator_routes())
            client = TestClient(app)
            response = client.get("/api/operator/status")
    assert response.status_code == 200
    assert response.json() == snapshot
