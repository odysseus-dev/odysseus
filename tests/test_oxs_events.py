"""Tests for OXS event subscriber registry."""

from __future__ import annotations

import pytest

from core.extensions import events


@pytest.fixture(autouse=True)
def _clear_subscribers():
    events._subscribers.clear()
    yield
    events._subscribers.clear()


def test_wire_and_dispatch_invokes_handler():
    received = []
    events.wire_subscriptions("ext1", [("greet", lambda p: received.append(p))])
    invoked = events.dispatch_event("greet", {"k": "v"})
    assert invoked == 1
    assert received == [{"k": "v"}]


def test_multiple_extensions_all_receive():
    a, b = [], []
    events.wire_subscriptions("a", [("ev", lambda p: a.append(p))])
    events.wire_subscriptions("b", [("ev", lambda p: b.append(p))])
    events.dispatch_event("ev", "hi")
    assert a == ["hi"]
    assert b == ["hi"]


def test_unwire_removes_only_target_extension():
    a, b = [], []
    events.wire_subscriptions("a", [("ev", lambda p: a.append(p))])
    events.wire_subscriptions("b", [("ev", lambda p: b.append(p))])
    removed = events.unwire_subscriptions("a")
    assert removed == 1
    events.dispatch_event("ev", "x")
    assert a == []
    assert b == ["x"]


def test_unwire_clears_empty_buckets():
    events.wire_subscriptions("a", [("ev", lambda p: None)])
    assert "ev" in events._subscribers
    events.unwire_subscriptions("a")
    assert "ev" not in events._subscribers


def test_handler_exception_does_not_stop_others():
    a, b = [], []
    def boom(_):
        raise RuntimeError("kaboom")
    events.wire_subscriptions("a", [("ev", boom)])
    events.wire_subscriptions("b", [("ev", lambda p: b.append(p))])
    invoked = events.dispatch_event("ev", "x")
    assert invoked == 2
    assert b == ["x"]


def test_dispatch_unknown_event_is_noop():
    assert events.dispatch_event("never_subscribed") == 0


def test_list_subscriptions_summary():
    events.wire_subscriptions("a", [("ev1", lambda p: None)])
    events.wire_subscriptions("b", [("ev1", lambda p: None), ("ev2", lambda p: None)])
    summary = events.list_subscriptions()
    assert summary["ev1"] == ["a", "b"]
    assert summary["ev2"] == ["b"]


def test_wire_subscriptions_returns_count():
    n = events.wire_subscriptions("a", [
        ("e1", lambda p: None),
        ("e2", lambda p: None),
        ("e3", lambda p: None),
    ])
    assert n == 3