import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import companion.messages as M
import companion.routes as R
from companion.routes import setup_companion_routes


def _route(path, method):
    for route in setup_companion_routes().routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


class _JsonRequest(SimpleNamespace):
    async def json(self):
        return self.payload


def _request(payload=None, owner="alice"):
    return _JsonRequest(
        payload=payload or {},
        state=SimpleNamespace(api_token=True, api_token_owner=owner),
        headers={},
    )


@pytest.fixture
def message_store(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "MESSAGES_FILE", str(tmp_path / "messages.json"))
    return tmp_path / "messages.json"


def test_message_queue_is_owner_scoped(message_store):
    alice = M.queue_outbound("alice", "+15551234567", "hi", "imessage")
    M.queue_outbound("bob", "+15557654321", "secret", "sms")

    assert M.pending_outbound("alice") == [alice]
    assert M.pending_outbound("bob")[0]["body"] == "secret"

    updated = M.mark_outbound("alice", alice["id"], "sent")
    assert updated["status"] == "sent"
    assert M.pending_outbound("alice") == []


def test_message_routes_do_not_require_macos(message_store, monkeypatch):
    monkeypatch.setattr(R, "get_current_user", lambda request: "alice")

    send = _route("/api/companion/messages/send", "POST")
    outbox = _route("/api/companion/messages/outbox", "GET")
    status = _route("/api/companion/messages/{message_id}/status", "POST")

    queued_response = asyncio.run(send(_request({"to": "+15551234567", "body": "hello"})))
    queued = queued_response["queued"]
    assert queued["service"] == "imessage"
    assert queued["status"] == "queued"

    assert outbox(_request())["messages"][0]["id"] == queued["id"]

    ack = asyncio.run(status(queued["id"], _request({"status": "sent"})))
    assert ack["message"]["status"] == "sent"
    assert outbox(_request())["messages"] == []


def test_message_routes_reject_unknown_owner(message_store):
    send = _route("/api/companion/messages/send", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(send(_request({"to": "+15551234567", "body": "hello"}, owner=None)))
    assert exc.value.status_code == 403
