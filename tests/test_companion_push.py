"""Companion mobile-push bridge: token store, owner-scoping, and event routing.

Pins that push tokens are stored per-owner (a caller never sees another's
device), that only owner-bearing mapped events produce a notification, that a
fired event reaches exactly the owner's devices, and that the generic
WebhookManager sink hook is isolated from failures.
"""

import asyncio
import sys
from unittest.mock import MagicMock

import pytest

# webhook_manager imports ``Webhook`` from the stubbed src.database; teach the
# stub that name so the module imports without a real DB (see
# tests/test_webhook_event_bridge.py for the same shim).
if "src.database" in sys.modules and not hasattr(sys.modules["src.database"], "Webhook"):
    sys.modules["src.database"].Webhook = MagicMock()

from companion import push


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
    # No cross-owner leakage, and an unknown owner sees nothing.
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
    # Removing the last token drops the owner entirely.
    push.unregister_push_token("alice", "ExponentPushToken[p2]")
    assert push.list_push_tokens("alice") == []


def test_register_rejects_bad_token(store):
    with pytest.raises(ValueError):
        push.register_push_token("alice", "garbage")
    with pytest.raises(ValueError):
        push.register_push_token("", "ExponentPushToken[p1]")


def test_notification_mapping():
    assert push.notification_for("research.completed", {}) == (
        "Research complete",
        "Your research report is ready.",
    )
    # Events that are not owner-routable notifications are skipped.
    assert push.notification_for("session.created", {}) is None
    assert push.notification_for("chat.message", {}) is None
    assert push.notification_for("totally.unknown", {}) is None


def test_push_event_delivers_only_to_owner(store, captured_deliveries):
    push.register_push_token("alice", "ExponentPushToken[alice-phone]")
    push.register_push_token("bob", "ExponentPushToken[bob-phone]")

    asyncio.run(push.push_event("research.completed", {"owner": "alice"}))

    assert len(captured_deliveries) == 1
    messages = captured_deliveries[0]
    assert [m["to"] for m in messages] == ["ExponentPushToken[alice-phone]"]
    assert messages[0]["title"] == "Research complete"
    assert messages[0]["data"] == {"event": "research.completed"}


def test_push_event_noop_without_owner_or_tokens(store, captured_deliveries):
    # No owner in payload → nothing delivered.
    asyncio.run(push.push_event("research.completed", {"session_id": "s1"}))
    # Owner present but no registered device → nothing delivered.
    asyncio.run(push.push_event("research.completed", {"owner": "nobody"}))
    # Owner with a device but an unmapped event → nothing delivered.
    push.register_push_token("alice", "ExponentPushToken[a]")
    asyncio.run(push.push_event("chat.message", {"owner": "alice"}))

    assert captured_deliveries == []


def test_send_test_push_counts_devices(store, captured_deliveries):
    assert asyncio.run(push.send_test_push("alice")) == 0  # none registered
    push.register_push_token("alice", "ExponentPushToken[a1]")
    push.register_push_token("alice", "ExponentPushToken[a2]")
    assert asyncio.run(push.send_test_push("alice")) == 2
    assert len(captured_deliveries) == 1
    assert {m["to"] for m in captured_deliveries[0]} == {
        "ExponentPushToken[a1]",
        "ExponentPushToken[a2]",
    }


def test_webhook_sink_registration_and_isolation():
    """add_sink registers the callable; _run_sink swallows a sink's exception so
    one bad sink can't break event handling."""
    from src.webhook_manager import WebhookManager

    wm = WebhookManager()
    seen = []

    async def good(event, payload):
        seen.append((event, payload))

    async def boom(event, payload):
        raise RuntimeError("sink exploded")

    wm.add_sink(good)
    wm.add_sink(boom)
    assert wm._sinks == [good, boom]

    asyncio.run(wm._run_sink(good, "research.completed", {"owner": "alice"}))
    assert seen == [("research.completed", {"owner": "alice"})]
    # Must not raise.
    asyncio.run(wm._run_sink(boom, "research.completed", {"owner": "alice"}))
