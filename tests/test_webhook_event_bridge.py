"""Pin the event-bus → outbound-webhook bridge.

Lifecycle events like ``research_completed`` are emitted from many scattered
handlers (``research_handler.py``, ``task_scheduler.py``, ``document_routes.py``,
``memory_routes.py``, ``ai_interaction.py``, ``email_routes.py``,
``skills_routes.py``, ``tool_implementations.py``) — none of which hold a
``webhook_manager`` reference. They all funnel through ``event_bus.fire_event``,
so the bus mirrors them to subscribed webhooks from that single seam.

These tests confirm the mapping fires the right public event name, that events
already delivered as webhooks at their own call sites are NOT bridged again
(no double-delivery), and that the new names are actually subscribable.
"""

import sys
from unittest.mock import MagicMock

# conftest.py stubs src.database with a fake module exposing only SessionLocal /
# ModelEndpoint; webhook_manager also imports ``Webhook`` from it. Teach the
# stub that name so the manager imports without touching the real DB.
if "src.database" in sys.modules and not hasattr(sys.modules["src.database"], "Webhook"):
    sys.modules["src.database"].Webhook = MagicMock()

from src import event_bus
from src import webhook_manager


class _FakeWebhookManager:
    """Records fire_and_forget calls instead of doing HTTP."""

    def __init__(self):
        self.calls = []

    def fire_and_forget(self, event, payload):
        self.calls.append((event, payload))


def _with_manager(manager):
    """Register a manager and return a cleanup callable to restore the default."""
    event_bus.set_webhook_manager(manager)
    return lambda: event_bus.set_webhook_manager(None)


def test_internal_events_bridge_to_webhook_names():
    """Each bridged internal event fires its public counterpart with the owner."""
    fake = _FakeWebhookManager()
    restore = _with_manager(fake)
    try:
        for internal in event_bus._WEBHOOK_EVENT_NAMES:
            event_bus._dispatch_webhook(internal, "alice")
        assert fake.calls == [
            ("research.completed", {"owner": "alice"}),
            ("document.created", {"owner": "alice"}),
            ("memory.added", {"owner": "alice"}),
            ("email.received", {"owner": "alice"}),
            ("skill.added", {"owner": "alice"}),
        ]
    finally:
        restore()


def test_already_webhooked_events_are_not_bridged():
    """session_created / message_sent already fire session.created / chat.message
    at their rich-data call sites — the bus must not double-deliver them."""
    fake = _FakeWebhookManager()
    restore = _with_manager(fake)
    try:
        event_bus._dispatch_webhook("session_created", "alice")
        event_bus._dispatch_webhook("message_sent", "alice")
        event_bus._dispatch_webhook("totally_unknown_event", "alice")
        assert fake.calls == []
    finally:
        restore()


def test_blank_owner_normalizes_to_none():
    """A blank/whitespace owner becomes null rather than an empty string."""
    fake = _FakeWebhookManager()
    restore = _with_manager(fake)
    try:
        event_bus._dispatch_webhook("memory_added", "   ")
        event_bus._dispatch_webhook("memory_added", None)
        assert fake.calls == [
            ("memory.added", {"owner": None}),
            ("memory.added", {"owner": None}),
        ]
    finally:
        restore()


def test_no_manager_is_a_safe_noop():
    """Before app.py wires a manager (or in single-purpose tooling), dispatch
    must not raise."""
    restore = _with_manager(None)
    try:
        event_bus._dispatch_webhook("research_completed", "alice")  # must not raise
    finally:
        restore()


def test_bridge_failure_never_propagates():
    """A misbehaving manager must not break the event's primary trigger path."""
    class _Boom:
        def fire_and_forget(self, event, payload):
            raise RuntimeError("delivery layer exploded")

    restore = _with_manager(_Boom())
    try:
        event_bus._dispatch_webhook("research_completed", "alice")  # swallowed
    finally:
        restore()


def test_new_events_are_subscribable():
    """The bridged public names must be valid webhook subscription targets."""
    for public in event_bus._WEBHOOK_EVENT_NAMES.values():
        assert public in webhook_manager.ALLOWED_EVENTS
    cleaned = webhook_manager.validate_events(
        "research.completed, document.created, memory.added"
    )
    assert cleaned == "research.completed,document.created,memory.added"


def test_fire_event_dispatches_webhook_synchronously():
    """fire_event itself drives the bridge (not just the private helper), so the
    eight emit sites get webhook mirroring for free."""
    fake = _FakeWebhookManager()
    restore = _with_manager(fake)
    try:
        # The webhook dispatch is synchronous and runs up front, *before* the
        # task-trigger path. With no running loop, fire_event then falls back to
        # asyncio.run(_handle_event), which needs the real DB — not under test
        # here — so swallow that tail and assert the dispatch already happened.
        try:
            event_bus.fire_event("skill_added", "bob")
        except Exception:
            pass
        assert ("skill.added", {"owner": "bob"}) in fake.calls
    finally:
        restore()
