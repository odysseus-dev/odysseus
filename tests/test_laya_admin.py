"""M3 tests: admin monitoring endpoints, pause/resume runtime switch, manual run,
admin gating, and the HTML panel.

Run: python -m pytest tests/test_laya_admin.py
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.laya import service as service_mod
from services.laya.config import LayaConfig
from services.laya.service import LayaService, is_paused, set_paused
from routes.laya_routes import setup_laya_routes


class _Client:
    def __init__(self, resp=None):
        self.resp = resp or {"status": "ok", "models": ["english"], "device": "cpu"}

    async def health(self):
        return self.resp

    async def systemone(self, state, questions, **kw):
        return {"answers": {"tier": {"choice": "small", "answer_confidence": 0.8}},
                "routing": {"model": "english"}}

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # isolate the process-global singleton + pause switch per test
    monkeypatch.setattr(service_mod.audit, "record", lambda *a, **k: None)
    yield
    service_mod._service = None
    set_paused(False)


def _client(monkeypatch, *, enabled=True, auth="false"):
    monkeypatch.setenv("AUTH_ENABLED", auth)
    svc = LayaService(config=LayaConfig(enabled=enabled, api_key="sekret"), client=_Client())
    monkeypatch.setattr(service_mod, "_service", svc)
    app = FastAPI()
    app.include_router(setup_laya_routes())
    return TestClient(app, raise_server_exceptions=False)


# ------------------------------ pause switch (unit) ------------------------------

def test_pause_toggles_active():
    svc = LayaService(config=LayaConfig(enabled=True), client=_Client())
    assert svc.active is True
    set_paused(True)
    try:
        assert is_paused() is True and svc.active is False
    finally:
        set_paused(False)
    assert svc.active is True


# ------------------------------ status / runs ------------------------------

def test_status_shape_and_no_key_leak(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    d = c.get("/api/laya/status").json()
    assert d["enabled"] is True and d["active"] is True and d["paused"] is False
    assert d["health"]["reachable"] is True
    assert d["config"]["api_key_set"] is True
    assert "api_key" not in d["config"]            # secret never serialized
    assert set(d["capabilities"]) == {"route", "guard", "moderate", "triage"}
    assert "summary" in d


def test_runs_endpoint_validates_capability(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/api/laya/runs").status_code == 200
    assert "runs" in c.get("/api/laya/runs").json()
    assert c.get("/api/laya/runs?capability=bogus").status_code == 400


# ------------------------------ pause / resume routes ------------------------------

def test_pause_resume_endpoints(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    assert c.post("/api/laya/pause").json() == {"paused": True}
    assert c.get("/api/laya/status").json()["active"] is False
    assert c.post("/api/laya/resume").json() == {"paused": False}
    assert c.get("/api/laya/status").json()["active"] is True


# ------------------------------ manual run ------------------------------

def test_manual_run_ok(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    r = c.post("/api/laya/run", json={"capability": "route", "text": "refactor this"})
    assert r.status_code == 200
    body = r.json()
    assert body["capability"] == "route" and body["ok"] is True
    assert body["acted"] is False          # manual runs are observe-only


def test_manual_run_validation(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    assert c.post("/api/laya/run", json={"capability": "bogus", "text": "x"}).status_code == 400
    assert c.post("/api/laya/run", json={"capability": "route", "text": "  "}).status_code == 400


def test_manual_run_blocked_when_disabled(monkeypatch):
    c = _client(monkeypatch, enabled=False)
    assert c.post("/api/laya/run", json={"capability": "route", "text": "x"}).status_code == 409


def test_manual_run_blocked_when_paused(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    c.post("/api/laya/pause")
    assert c.post("/api/laya/run", json={"capability": "route", "text": "x"}).status_code == 409


# ------------------------------ admin gate + panel ------------------------------

def test_admin_endpoints_require_admin(monkeypatch):
    c = _client(monkeypatch, enabled=True, auth="true")  # no auth_manager on app.state => 403
    assert c.get("/api/laya/status").status_code == 403
    assert c.post("/api/laya/pause").status_code == 403
    assert c.get("/laya/admin").status_code == 403


def test_admin_panel_served(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    r = c.get("/laya/admin")
    assert r.status_code == 200
    assert "laya decision engine" in r.text
    assert "{{CSP_NONCE}}" not in r.text        # placeholder was substituted
