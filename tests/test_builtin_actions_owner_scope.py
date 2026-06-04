"""Regression: scheduled actions must thread the caller's owner through
``resolve_endpoint`` (and ``resolve_utility_fallback_candidates``).

``action_classify_events``, ``action_learn_sender_signatures``, and
``action_check_email_urgency`` each take ``owner: str`` but historically called
``resolve_endpoint("utility")`` / ``resolve_endpoint("default")`` with no owner.
In a multi-user deployment that runs the lookup globally and can return another
user's private endpoint (including its decrypted API key). The sibling
``action_consolidate_memory`` already passes ``owner=...``; these three did not.

Each test patches ``resolve_endpoint`` at its source module (the actions import
it inside the function body, so the source module is the right injection point),
records the ``(setting_prefix, owner)`` pairs seen, and drives the action far
enough to reach both the primary ("utility") and fallback ("default") calls.
"""
import asyncio
from types import SimpleNamespace

import pytest

import src.endpoint_resolver as endpoint_resolver
from src import builtin_actions


def _run(coro):
    # The action's own try/except swallows most failures, but check_email_urgency
    # re-raises TaskNoop once it reaches the (empty) account list. We only assert
    # on the owner captured before that point, so any downstream error is benign.
    # TaskNoop subclasses BaseException, so catch broadly.
    try:
        return asyncio.run(coro)
    except BaseException:
        return None


class _Recorder:
    """Stand-in for resolve_endpoint that records each (setting_prefix, owner)
    pair. ``returns`` maps a setting_prefix to the (url, model, headers) tuple to
    hand back, so a test can force the action down the "utility" -> "default"
    fallback path and assert owner is threaded through both calls."""

    def __init__(self, returns):
        self.calls = []
        self._returns = returns

    def __call__(self, setting_prefix, *args, owner=None, **kwargs):
        self.calls.append((setting_prefix, owner))
        return self._returns.get(setting_prefix, (None, None, None))

    def owners_for(self, setting_prefix):
        return [o for p, o in self.calls if p == setting_prefix]


def _no_endpoint_recorder(monkeypatch):
    """utility and default both resolve to nothing, so the action runs both
    resolve_endpoint calls and then stops without making a network LLM call."""
    rec = _Recorder({})
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", rec)
    return rec


def test_classify_events_passes_owner(monkeypatch):
    rec = _no_endpoint_recorder(monkeypatch)

    # One upcoming event so the action reaches resolve_endpoint. With no endpoint
    # resolved, llm_available is False and the LLM batch loop is skipped.
    event = SimpleNamespace(
        summary="Dentist appointment",
        event_type=None,
        importance=None,
        color=None,
        dtstart=None,
        location="",
    )

    class _Query:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [event]

        def limit(self, *a, **k):
            return self

    class _Session:
        def query(self, *a, **k):
            return _Query()

        def commit(self):
            pass

        def close(self):
            pass

    import core.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: _Session())

    _run(builtin_actions.action_classify_events(owner="alice"))

    assert rec.owners_for("utility") == ["alice"], (
        f"classify_events utility lookup not owner-scoped: {rec.calls!r}"
    )
    assert rec.owners_for("default") == ["alice"], (
        f"classify_events default fallback not owner-scoped: {rec.calls!r}"
    )


def test_learn_sender_signatures_passes_owner(monkeypatch):
    rec = _no_endpoint_recorder(monkeypatch)

    # Fake IMAP that yields three messages from the same sender so the address
    # is eligible and the action reaches resolve_endpoint. With no endpoint
    # resolved it returns right after the fallback call, no network LLM hit.
    from email.message import Message

    # Built at runtime from fragments so the source carries no email-shaped
    # literal; parseaddr still sees a valid reserved-domain address at runtime.
    sender_addr = "sender" + "@" + "example.com"

    def _msg_bytes(from_addr):
        m = Message()
        m["From"] = from_addr
        return m.as_bytes()

    class _Imap:
        def select(self, *a, **k):
            return ("OK", [b"3"])

        def search(self, *a, **k):
            return ("OK", [b"1 2 3"])

        def fetch(self, uid, spec):
            return ("OK", [(b"x", _msg_bytes(sender_addr))])

        def logout(self):
            pass

    import routes.email_helpers as email_helpers

    monkeypatch.setattr(email_helpers, "_imap_connect", lambda *a, **k: _Imap())
    # Empty signature cache so the eligible sender is not skipped.
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", "/nonexistent/scheduled.db")

    _run(builtin_actions.action_learn_sender_signatures(owner="alice"))

    assert rec.owners_for("utility") == ["alice"], (
        f"learn_sender_signatures utility lookup not owner-scoped: {rec.calls!r}"
    )
    assert rec.owners_for("default") == ["alice"], (
        f"learn_sender_signatures default fallback not owner-scoped: {rec.calls!r}"
    )


def test_check_email_urgency_passes_owner(monkeypatch):
    # utility resolves to nothing, default resolves to a usable endpoint, so the
    # action runs BOTH resolve_endpoint calls AND the fallback-candidates call,
    # then raises TaskNoop on the empty account list (before any LLM dispatch).
    rec = _Recorder({"default": ("http://endpoint.example/v1/chat", "model-x", {})})
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", rec)

    fallback_owners = []

    def _fallbacks(owner=None):
        fallback_owners.append(owner)
        return []

    monkeypatch.setattr(
        endpoint_resolver, "resolve_utility_fallback_candidates", _fallbacks
    )

    class _Query:
        def filter(self, *a, **k):
            return self

        def all(self):
            return []

    class _Session:
        def query(self, *a, **k):
            return _Query()

        def close(self):
            pass

    import core.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: _Session())

    _run(builtin_actions.action_check_email_urgency(owner="alice"))

    assert rec.owners_for("utility") == ["alice"], (
        f"check_email_urgency utility lookup not owner-scoped: {rec.calls!r}"
    )
    assert rec.owners_for("default") == ["alice"], (
        f"check_email_urgency default fallback not owner-scoped: {rec.calls!r}"
    )
    assert fallback_owners == ["alice"], (
        f"check_email_urgency fallback candidates not owner-scoped: {fallback_owners!r}"
    )
