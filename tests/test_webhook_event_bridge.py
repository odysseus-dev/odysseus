"""Pin the event-bus → outbound-webhook bridge.

Lifecycle events are emitted from many scattered handlers that hold no
``webhook_manager`` reference; they all funnel through ``event_bus.fire_event``,
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
    """fire_event itself drives the bridge (not just the private helper), so every
    emit site gets webhook mirroring for free."""
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


# ---------------------------------------------------------------------------
# Email-MCP draft bridge: the email server runs as a subprocess, so its own
# fire_event("document_created") lands on a manager-less bus. McpManager re-emits
# it in the app process after a draft tool succeeds. These drive that real seam
# (a fake MCP session), not just the same-process event-bus helper.
# ---------------------------------------------------------------------------

import asyncio
import types


class _FakeContent:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class _FakeMcpResult:
    def __init__(self, text, is_error=False):
        self.content = [_FakeContent(text)]
        self.isError = is_error


class _FakeSession:
    """Stands in for a live MCP ClientSession; echoes a canned tool result."""

    def __init__(self, text, is_error=False):
        self._result = _FakeMcpResult(text, is_error)
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self._result


def _run_email_tool(tool_name, text, *, owner="alice", is_error=False):
    """Call McpManager.call_tool against a fake email session, capturing any
    bridged webhook. Returns the list of (event, payload) the manager fired."""
    from src.mcp_manager import McpManager

    fake_wh = _FakeWebhookManager()
    restore = _with_manager(fake_wh)
    # Keep the trigger path off the real DB/AUTH — we only assert the webhook seam.
    orig_handle = event_bus._handle_event

    async def _noop(*a, **k):
        return None

    event_bus._handle_event = _noop
    try:
        mgr = McpManager()
        mgr._sessions["email"] = _FakeSession(text, is_error=is_error)
        args = {"_odysseus_owner": owner, "to": "x@y.z", "subject": "hi"}
        asyncio.run(mgr.call_tool(f"mcp__email__{tool_name}", args))
        return fake_wh.calls
    finally:
        event_bus._handle_event = orig_handle
        restore()


def test_email_draft_bridges_document_created_with_owner():
    """A successful draft_email (subprocess) fires document.created in-app,
    scoped to the owner the app passed down via _odysseus_owner."""
    calls = _run_email_tool("draft_email", "Created Odysseus email draft `Hi`.")
    assert calls == [("document.created", {"owner": "alice"})]


def test_email_reply_draft_bridges_document_created():
    """The reply-draft tools also create a compose document → same bridge."""
    for tool in ("draft_email_reply", "ai_draft_email_reply"):
        calls = _run_email_tool(tool, "Created Odysseus reply draft `Re: Hi`.")
        assert calls == [("document.created", {"owner": "alice"})], tool


def test_email_send_does_not_bridge_document_created():
    """send_email creates no document — it must not fire document.created."""
    calls = _run_email_tool("send_email", "Sent email to x@y.z with subject 'hi'.")
    assert calls == []


def test_email_draft_error_result_does_not_bridge():
    """The email handler reports failures as an 'Error: ...' string at exit_code 0;
    the bridge must gate on the success text, not merely a clean exit code."""
    calls = _run_email_tool("draft_email", "Error: no account configured")
    assert calls == []
