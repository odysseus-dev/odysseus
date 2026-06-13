# tests/test_platform_routes.py
"""Platform routes: admin-gated registry, envelope ingest, approvals.

require_admin (core/middleware) passes when app.state.auth_manager is
configured and reports the current_user as admin; it does NOT look at
request.state.is_admin. The fixture therefore stamps current_user and
provides a stub auth_manager (we adjust the TEST, never weaken require_admin).
get_current_user returns request.state.current_user, so the approval
endpoints see "human:oleg" — the manager of the ORIGIN company.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.platform_routes import setup_platform_routes
from services.business_platform.envelope import Envelope, sign_envelope
from services.business_platform.registry import create_company, company_private_key


class _StubAuthMgr:
    is_configured = True

    def is_admin(self, user):
        return True


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(setup_platform_routes())
    app.state.auth_manager = _StubAuthMgr()

    # Simulate the auth middleware: stamp the current user on request.state.
    @app.middleware("http")
    async def fake_auth(request, call_next):
        request.state.current_user = "human:oleg"
        return await call_next(request)

    return TestClient(app)


def test_company_crud_roundtrip(client):
    r = client.post("/api/platform/companies", json={
        "id": "rt-c1", "vertical_type": "travel_agency",
        "display_name": "RT One", "manager_principal_id": "human:oleg",
    })
    assert r.status_code == 200, r.text
    r2 = client.get("/api/platform/companies/rt-c1")
    assert r2.status_code == 200
    assert r2.json()["vertical_type"] == "travel_agency"


def test_envelope_ingest_endpoint_and_approval_flow(client):
    client.post("/api/platform/companies", json={
        "id": "rt-c2", "vertical_type": "travel_agency",
        "display_name": "RT Two", "manager_principal_id": "human:oleg"})
    client.post("/api/platform/companies", json={
        "id": "rt-c3", "vertical_type": "travel_agency",
        "display_name": "RT Three", "manager_principal_id": "human:other"})
    env = Envelope(
        message_id="rt-m1", conversation_id="c-rt", idempotency_key="rt-m1",
        from_company="rt-c2", to_company="rt-c3",
        issued_at="2026-06-13T10:00:00Z", intent="booking.confirm",
        status="proposed", payload={"booking_id": "B1"})
    sig = sign_envelope(env, company_private_key("rt-c2"))
    r = client.post("/api/platform/envelopes",
                    json={"envelope": env.model_dump(mode="json"),
                          "signature": sig})
    assert r.status_code == 200 and r.json()["gated"] is True

    r2 = client.get("/api/platform/approvals")
    items = r2.json()
    target = [i for i in items if i["envelope_message_id"] == "rt-m1"]
    assert target
    r3 = client.post(f"/api/platform/approvals/{target[0]['id']}/approve")
    assert r3.status_code == 200 and r3.json()["state"] == "approved"


def test_bad_signature_is_400(client):
    client.post("/api/platform/companies", json={
        "id": "rt-c4", "vertical_type": "travel_agency",
        "display_name": "RT Four", "manager_principal_id": "human:oleg"})
    env = Envelope(
        message_id="rt-m2", conversation_id="c-rt", idempotency_key="rt-m2",
        from_company="rt-c4", to_company="bigboss",
        issued_at="2026-06-13T10:00:00Z", intent="status.report",
        status="finished", payload={})
    r = client.post("/api/platform/envelopes",
                    json={"envelope": env.model_dump(mode="json"),
                          "signature": "00" * 64})
    assert r.status_code == 400
