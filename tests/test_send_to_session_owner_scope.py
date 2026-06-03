"""Regression guard for #1616 — the send_to_session agent tool must be owner-scoped.

`do_send_to_session` resolved any session id via `_session_manager.get_session()`
with no ownership check, then read its history and appended messages. The agent
dispatcher also did not pass `owner=` (every sibling session tool does). Together,
an agent acting for user A could read from and write into user B's session.

The fix threads `owner` through `dispatch_ai_tool` and rejects a session owned by
someone else — returning the same "not found" message so ids can't be probed.
`owner=None` (single-user / no-auth installs) is a no-op, preserving behavior.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

import src.ai_interaction as ai


class _StubSession:
    def __init__(self, owner):
        self.owner = owner
        self.name = "target"
        self.endpoint_url = "http://x"
        self.model = "m"
        self.headers = {}
        self.added = []

    def get_context_messages(self):
        return []

    def add_message(self, msg):
        self.added.append(msg)


class _StubManager:
    def __init__(self, session):
        self._session = session

    def get_session(self, sid):
        return self._session


def _patch(monkeypatch, session):
    async def _fake_llm(*args, **kwargs):
        return "assistant reply"

    # do_send_to_session does `from src.llm_core import llm_call_async` at call
    # time, so patching the attribute on the module is sufficient.
    monkeypatch.setattr("src.llm_core.llm_call_async", _fake_llm)
    monkeypatch.setattr(ai, "_session_manager", _StubManager(session))
    return session


async def test_dispatch_rejects_cross_owner(monkeypatch):
    # Drives the full dispatcher path: catches BOTH the missing `owner=` at
    # dispatch and the missing ownership guard inside do_send_to_session.
    sess = _patch(monkeypatch, _StubSession(owner="bob"))
    desc, result = await ai.dispatch_ai_tool(
        "send_to_session", "sid123\nhello", None, owner="alice"
    )
    assert "error" in result, "alice must not reach bob's session via the agent tool"
    assert "not found" in result["error"].lower()
    assert sess.added == [], "no message may be written into another owner's session"


async def test_allows_same_owner(monkeypatch):
    sess = _patch(monkeypatch, _StubSession(owner="alice"))
    result = await ai.do_send_to_session("sid123\nhello", owner="alice")
    assert "error" not in result, result
    assert result["response"] == "assistant reply"
    assert len(sess.added) == 2  # user + assistant both persisted


async def test_rejects_legacy_null_owner_for_named_caller(monkeypatch):
    # Strict (mirrors do_manage_session): a named user cannot target a
    # legacy/shared owner=None session through this tool.
    sess = _patch(monkeypatch, _StubSession(owner=None))
    result = await ai.do_send_to_session("sid123\nhello", owner="alice")
    assert "error" in result
    assert sess.added == []


async def test_owner_none_is_noop_for_single_user(monkeypatch):
    # No-auth / single-user installs pass owner=None -> guard skipped ->
    # behavior unchanged even when the session carries an owner.
    sess = _patch(monkeypatch, _StubSession(owner="bob"))
    result = await ai.do_send_to_session("sid123\nhello", owner=None)
    assert "error" not in result, result
    assert len(sess.added) == 2
