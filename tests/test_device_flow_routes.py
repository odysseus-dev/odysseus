"""Shared device-flow route helper regressions."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routes import device_flow


def _client(monkeypatch, now_ref, start_flow, poll_flow):
    store = device_flow.PendingDeviceFlowStore(time_func=lambda: now_ref[0])
    router = device_flow.create_device_flow_router(
        prefix="/api/test-device",
        tags=["test-device"],
        store=store,
        start_flow=start_flow,
        poll_flow=poll_flow,
    )
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(device_flow, "require_admin", lambda request: None)
    return TestClient(app)


def _start(_request, _form):
    return device_flow.DeviceFlowStart(
        pending={"secret": "server-only", "owner": "alice"},
        response={"user_code": "ABCD-EFGH", "verification_uri": "https://example.test/device"},
        interval=5,
        expires_in=20,
    )


def test_pending_poll_is_throttled_until_interval(monkeypatch):
    now = [100.0]
    calls = []

    def poll(_request, pending):
        calls.append(dict(pending))
        return device_flow.DeviceFlowPoll.pending()

    client = _client(monkeypatch, now, _start, poll)
    start = client.post("/api/test-device/device/start").json()

    first = client.post("/api/test-device/device/poll", data={"poll_id": start["poll_id"]})
    assert first.json() == {"status": "pending"}
    assert calls == [{"secret": "server-only", "owner": "alice"}]

    second = client.post("/api/test-device/device/poll", data={"poll_id": start["poll_id"]})
    assert second.json() == {"status": "pending"}
    assert len(calls) == 1

    now[0] += 5
    third = client.post("/api/test-device/device/poll", data={"poll_id": start["poll_id"]})
    assert third.json() == {"status": "pending"}
    assert len(calls) == 2


def test_slow_down_updates_poll_interval(monkeypatch):
    now = [100.0]
    calls = []

    def poll(_request, _pending):
        calls.append(now[0])
        if len(calls) == 1:
            return device_flow.DeviceFlowPoll.slow_down(interval=10)
        return device_flow.DeviceFlowPoll.authorized({"id": "ep1", "models": ["gpt-4o"]})

    client = _client(monkeypatch, now, _start, poll)
    poll_id = client.post("/api/test-device/device/start").json()["poll_id"]

    assert client.post("/api/test-device/device/poll", data={"poll_id": poll_id}).json() == {"status": "pending"}
    now[0] += 9
    assert client.post("/api/test-device/device/poll", data={"poll_id": poll_id}).json() == {"status": "pending"}
    assert len(calls) == 1

    now[0] += 1
    assert client.post("/api/test-device/device/poll", data={"poll_id": poll_id}).json() == {
        "status": "authorized",
        "endpoint": {"id": "ep1", "models": ["gpt-4o"]},
    }


def test_authorized_and_failed_polls_remove_pending_session(monkeypatch):
    now = [100.0]
    outcomes = [
        device_flow.DeviceFlowPoll.authorized({"id": "ep1"}),
        device_flow.DeviceFlowPoll.failed("access_denied"),
    ]

    def poll(_request, _pending):
        return outcomes.pop(0)

    client = _client(monkeypatch, now, _start, poll)
    first = client.post("/api/test-device/device/start").json()["poll_id"]
    second = client.post("/api/test-device/device/start").json()["poll_id"]

    assert client.post("/api/test-device/device/poll", data={"poll_id": first}).json()["status"] == "authorized"
    assert client.post("/api/test-device/device/poll", data={"poll_id": first}).status_code == 404

    assert client.post("/api/test-device/device/poll", data={"poll_id": second}).json() == {
        "status": "failed",
        "error": "access_denied",
    }
    assert client.post("/api/test-device/device/poll", data={"poll_id": second}).status_code == 404


def test_cancel_and_expiry_remove_pending_session(monkeypatch):
    now = [100.0]

    def poll(_request, _pending):
        return device_flow.DeviceFlowPoll.pending()

    client = _client(monkeypatch, now, _start, poll)
    cancelled = client.post("/api/test-device/device/start").json()["poll_id"]
    assert client.post("/api/test-device/device/cancel", data={"poll_id": cancelled}).json() == {"status": "cancelled"}
    assert client.post("/api/test-device/device/poll", data={"poll_id": cancelled}).status_code == 404

    expired = client.post("/api/test-device/device/start").json()["poll_id"]
    now[0] += 21
    assert client.post("/api/test-device/device/poll", data={"poll_id": expired}).status_code == 404


def test_routes_are_admin_gated(monkeypatch):
    now = [100.0]

    def poll(_request, _pending):
        return device_flow.DeviceFlowPoll.pending()

    client = _client(monkeypatch, now, _start, poll)

    def deny(_request):
        raise HTTPException(403, "admin required")

    monkeypatch.setattr(device_flow, "require_admin", deny)
    assert client.post("/api/test-device/device/start").status_code == 403
    assert client.post("/api/test-device/device/poll", data={"poll_id": "missing"}).status_code == 403
    assert client.post("/api/test-device/device/cancel", data={"poll_id": "missing"}).status_code == 403
