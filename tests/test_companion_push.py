"""Companion mobile-push bridge: token store, owner-scoping, and event routing.

Pins that push tokens are stored per-owner (a caller never sees another's
device), that only owner-bearing mapped lifecycle events produce a notification,
that a fired event reaches exactly the owner's devices END-TO-END through the
event bus (the real producer path, not an adjacent bridge), that the push routes
enforce the companion scope and reject malformed bodies, and that account
rename/deletion migrate/purge the store.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion import push
from companion.routes import setup_companion_routes


@pytest.fixture
def store(tmp_path):
    """Point the JSON token store at a temp file for the duration of a test."""
    push._store_path_override = str(tmp_path / "companion_push.json")
    try:
        yield
    finally:
        push._store_path_override = None


@pytest.fixture
def captured_deliveries(monkeypatch):
    """Capture Expo deliveries instead of hitting the network."""
    sent = []

    async def _fake_deliver(messages):
        sent.append(messages)

    monkeypatch.setattr(push, "deliver", _fake_deliver)
    return sent


# --- token store: owner-scoped ---------------------------------------------

def test_token_validation():
    assert push.is_valid_token("ExponentPushToken[abc123-_.XYZ]")
    assert push.is_valid_token("ExpoPushToken[abc123]")
    assert not push.is_valid_token("")
    assert not push.is_valid_token("not-a-token")
    assert not push.is_valid_token("ExponentPushToken[]")
    assert not push.is_valid_token("ExponentPushToken[abc]; rm -rf /")


def test_register_is_owner_scoped(store):
    push.register_push_token("alice", "ExponentPushToken[alice-phone]")
    push.register_push_token("bob", "ExponentPushToken[bob-phone]")

    assert push.list_push_tokens("alice") == ["ExponentPushToken[alice-phone]"]
    assert push.list_push_tokens("bob") == ["ExponentPushToken[bob-phone]"]
    assert push.list_push_tokens("carol") == []


def test_register_is_idempotent_and_unregister(store):
    push.register_push_token("alice", "ExponentPushToken[p1]")
    push.register_push_token("alice", "ExponentPushToken[p1]")  # dup
    push.register_push_token("alice", "ExponentPushToken[p2]")
    assert push.list_push_tokens("alice") == [
        "ExponentPushToken[p1]",
        "ExponentPushToken[p2]",
    ]

    push.unregister_push_token("alice", "ExponentPushToken[p1]")
    assert push.list_push_tokens("alice") == ["ExponentPushToken[p2]"]
    push.unregister_push_token("alice", "ExponentPushToken[p2]")
    assert push.list_push_tokens("alice") == []


def test_register_rejects_bad_token(store):
    with pytest.raises(ValueError):
        push.register_push_token("alice", "garbage")
    with pytest.raises(ValueError):
        push.register_push_token("", "ExponentPushToken[p1]")


# --- account lifecycle: rename migrates, delete purges ---------------------

def test_rename_owner_moves_devices(store):
    push.register_push_token("alice", "ExponentPushToken[a1]")
    push.register_push_token("alice", "ExponentPushToken[a2]")
    push.rename_owner("alice", "alice2")
    assert push.list_push_tokens("alice") == []
    assert set(push.list_push_tokens("alice2")) == {
        "ExponentPushToken[a1]", "ExponentPushToken[a2]",
    }


def test_rename_owner_merges_into_existing_without_dupes(store):
    push.register_push_token("alice", "ExponentPushToken[shared]")
    push.register_push_token("bob", "ExponentPushToken[shared]")
    push.register_push_token("bob", "ExponentPushToken[bob-only]")
    push.rename_owner("alice", "bob")
    assert push.list_push_tokens("alice") == []
    assert push.list_push_tokens("bob").count("ExponentPushToken[shared]") == 1
    assert "ExponentPushToken[bob-only]" in push.list_push_tokens("bob")


def test_purge_owner_removes_devices_and_prevents_reuse_bleed(store):
    push.register_push_token("alice", "ExponentPushToken[a1]")
    push.purge_owner("alice")
    assert push.list_push_tokens("alice") == []
    # A reused username starts clean — no prior account's phone attached.
    assert push.list_push_tokens("alice") == []


# --- notification mapping keyed on INTERNAL event names --------------------

def test_notification_mapping_uses_internal_event_names():
    assert push.notification_for("research_completed") == (
        "Research complete",
        "Your research report is ready.",
    )
    assert push.notification_for("document_created")[0] == "Document added"
    # Non-lifecycle / dotted webhook names are NOT what producers fire → skipped.
    assert push.notification_for("session_created") is None
    assert push.notification_for("research.completed") is None
    assert push.notification_for("totally_unknown") is None


# --- sink delivery: (event, owner) contract --------------------------------

def test_push_event_delivers_only_to_owner(store, captured_deliveries):
    push.register_push_token("alice", "ExponentPushToken[alice-phone]")
    push.register_push_token("bob", "ExponentPushToken[bob-phone]")

    asyncio.run(push.push_event("research_completed", "alice"))

    assert len(captured_deliveries) == 1
    messages = captured_deliveries[0]
    assert [m["to"] for m in messages] == ["ExponentPushToken[alice-phone]"]
    assert messages[0]["title"] == "Research complete"
    assert messages[0]["data"] == {"event": "research_completed"}


def test_push_event_noop_without_owner_or_tokens(store, captured_deliveries):
    asyncio.run(push.push_event("research_completed", None))       # no owner
    asyncio.run(push.push_event("research_completed", "nobody"))    # owner, no device
    push.register_push_token("alice", "ExponentPushToken[a]")
    asyncio.run(push.push_event("chat_message", "alice"))          # unmapped event
    assert captured_deliveries == []


def test_send_test_push_counts_devices(store, captured_deliveries):
    assert asyncio.run(push.send_test_push("alice")) == 0
    push.register_push_token("alice", "ExponentPushToken[a1]")
    push.register_push_token("alice", "ExponentPushToken[a2]")
    assert asyncio.run(push.send_test_push("alice")) == 2
    assert len(captured_deliveries) == 1
    assert {m["to"] for m in captured_deliveries[0]} == {
        "ExponentPushToken[a1]", "ExponentPushToken[a2]",
    }


# --- END-TO-END: a fired lifecycle event reaches the owner's phone ---------
# This is the crux of the review: push is wired to the event bus (the producer
# path), so firing the event the app actually emits drives a delivery — no
# dependence on the outbound-webhook layer.

def test_fire_event_drives_push_sink_end_to_end(store, captured_deliveries, monkeypatch):
    from src import event_bus

    # Keep the task-trigger tail off the real DB/AUTH; we assert only the sink.
    async def _noop_handle(*a, **k):
        return None
    monkeypatch.setattr(event_bus, "_handle_event", _noop_handle)

    push.register_push_token("alice", "ExponentPushToken[alice-phone]")
    event_bus.add_event_sink(push.build_push_sink())
    try:
        # No running loop → _dispatch_event_sinks runs the sink synchronously.
        event_bus.fire_event("research_completed", "alice")
    finally:
        event_bus._event_sinks.clear()

    assert len(captured_deliveries) == 1
    assert [m["to"] for m in captured_deliveries[0]] == ["ExponentPushToken[alice-phone]"]


def test_event_sink_failure_is_isolated(monkeypatch):
    from src import event_bus

    async def _noop_handle(*a, **k):
        return None
    monkeypatch.setattr(event_bus, "_handle_event", _noop_handle)

    async def boom(event, owner):
        raise RuntimeError("sink exploded")

    event_bus.add_event_sink(boom)
    try:
        event_bus.fire_event("research_completed", "alice")  # must not raise
    finally:
        event_bus._event_sinks.clear()


# --- route layer: scope enforcement + malformed body -----------------------

def _push_handler(suffix):
    router = setup_companion_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith(suffix):
            return r.endpoint
    raise AssertionError(f"{suffix} route not found")


class _Req:
    def __init__(self, *, api_token, scopes=None, owner=None, current_user=None, body=None):
        self.state = SimpleNamespace(
            api_token=api_token, api_token_scopes=scopes,
            api_token_owner=owner, current_user=current_user,
        )
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_push_routes_require_companion_scope():
    # A bearer token WITHOUT the chat/companion scope is rejected on all three
    # state-changing push routes, before any store mutation.
    for suffix in ("/push/register", "/push/unregister", "/push/test"):
        req = _Req(api_token=True, scopes=["todos:read"], owner="alice",
                   body={"token": "ExponentPushToken[a]"})
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_push_handler(suffix)(req))
        assert exc.value.status_code == 403, suffix


def test_push_register_accepts_scoped_token(store):
    req = _Req(api_token=True, scopes=["chat", "companion"], owner="alice",
               body={"token": "ExponentPushToken[alice-phone]"})
    result = asyncio.run(_push_handler("/push/register")(req))
    assert result == {"ok": True, "devices": 1}
    assert push.list_push_tokens("alice") == ["ExponentPushToken[alice-phone]"]


@pytest.mark.parametrize("bad_body", [["not", "an", "object"], "a string", 42, None])
def test_push_register_rejects_non_object_json(store, bad_body):
    # Valid JSON that isn't an object must be a 400, never an AttributeError 500.
    req = _Req(api_token=True, scopes=["chat"], owner="alice", body=bad_body)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_push_handler("/push/register")(req))
    assert exc.value.status_code == 400


def test_push_register_rejects_non_string_token(store):
    req = _Req(api_token=True, scopes=["chat"], owner="alice", body={"token": 123})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_push_handler("/push/register")(req))
    assert exc.value.status_code == 400
