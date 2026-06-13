# tests/test_platform_mission_routes.py
"""Mission HTTP surface: create+dispatch, timeline, refresh. Plan 3 Task 4."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.platform_routes import setup_platform_routes
from services.business_platform import registry
from services.business_platform.envelope import Envelope, sign_envelope
from services.business_platform.hub import ingest
from services.business_platform import mission


class _StubAuthMgr:
    is_configured = True

    def is_admin(self, user):
        return True


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(setup_platform_routes())
    app.state.auth_manager = _StubAuthMgr()

    @app.middleware("http")
    async def fake_auth(request, call_next):
        request.state.current_user = "oleg"
        return await call_next(request)

    return TestClient(app)


def _reply(company, conversation_id):
    env = Envelope(
        message_id=f"reply-{uuid.uuid4().hex[:12]}",
        conversation_id=conversation_id,
        idempotency_key=f"reply-{uuid.uuid4().hex[:12]}",
        from_company=company, to_company=mission.BIG_BOSS_COMPANY,
        issued_at="2026-06-13T12:00:00Z", intent="status.report",
        status="finished", payload={"summary": "ok"})
    ingest(env, sign_envelope(env, registry.company_private_key(company)))


def test_create_get_refresh_mission(client):
    cid = f"mr-co-{uuid.uuid4().hex[:6]}"
    registry.create_company(cid, "general_office", cid,
                            manager_principal_id="human:oleg")
    r = client.post("/api/platform/missions", json={
        "goal": "grow traffic",
        "tasks": [{"company": cid, "intent": "status.report",
                   "task": "audit the site"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["tasks"][0]["status"] == "dispatched"
    mid = body["id"]
    conv = body["tasks"][0]["conversation_id"]

    g = client.get(f"/api/platform/missions/{mid}")
    assert g.status_code == 200 and g.json()["goal"] == "grow traffic"

    _reply(cid, conv)
    rr = client.post(f"/api/platform/missions/{mid}/refresh")
    assert rr.status_code == 200
    assert rr.json()["status"] == "completed"
    assert rr.json()["tasks"][0]["status"] == "completed"


def test_create_mission_validation_400(client):
    r = client.post("/api/platform/missions", json={"goal": "g", "tasks": []})
    assert r.status_code == 400


def test_get_unknown_mission_404(client):
    r = client.get("/api/platform/missions/nope")
    assert r.status_code == 404
