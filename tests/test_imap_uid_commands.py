"""Regression: IMAP calls must use uid() not search()/fetch().

conn.search() / conn.fetch() operate on volatile positional sequence
numbers that shift whenever messages are deleted or expunged. The
sig-learner and daily-brief actions must use conn.uid("SEARCH", ...)
and conn.uid("FETCH", ...) which address messages by their persistent
RFC 3501 UID (§2.3.1.1, §6.4.8).
"""
import pytest


class _SpyImap:
    """IMAP stub that records uid() calls and raises on search()/fetch()."""

    def __init__(self, uid_list=b"1 2 3"):
        self._uid_list = uid_list
        self.uid_calls: list[tuple] = []

    def select(self, *args, **kwargs):
        return "OK", []

    def uid(self, command, *args):
        self.uid_calls.append((command,) + args)
        if command == "SEARCH":
            return "OK", [self._uid_list]
        if command == "FETCH":
            query = args[1] if len(args) > 1 else ""
            if "HEADER.FIELDS" in query:
                return "OK", [(None, b"From: Writer <writer@example.com>\r\n"
                                     b"Subject: Hello\r\n\r\n")]
            return "OK", [(None, b"Body text\r\n\r\nRegards,\r\nThe Writer\r\n")]
        return "OK", []

    def search(self, *args):
        raise AssertionError("conn.search() called — must use conn.uid('SEARCH', ...) instead")

    def fetch(self, *args):
        raise AssertionError("conn.fetch() called — must use conn.uid('FETCH', ...) instead")

    def logout(self):
        pass


@pytest.mark.asyncio
async def test_sig_learner_uses_uid_search(monkeypatch):
    """_pull_headers must call conn.uid('SEARCH', ...) not conn.search()."""
    from routes import email_helpers
    from src import task_endpoint
    from src.builtin_actions import action_learn_sender_signatures

    spy = _SpyImap()
    monkeypatch.setattr(email_helpers, "_imap_connect", lambda *a, **kw: spy)
    monkeypatch.setattr(task_endpoint, "resolve_task_candidates", lambda *a, **kw: [])

    message, ok = await action_learn_sender_signatures("alice")

    assert ok is False  # no LLM candidates — stops before LLM, after IMAP
    assert any(c[0] == "SEARCH" for c in spy.uid_calls), "uid('SEARCH', ...) was not called"


@pytest.mark.asyncio
async def test_sig_learner_uses_uid_fetch(monkeypatch):
    """_pull_headers must call conn.uid('FETCH', ...) not conn.fetch()."""
    from routes import email_helpers
    from src import task_endpoint
    from src.builtin_actions import action_learn_sender_signatures

    spy = _SpyImap()
    monkeypatch.setattr(email_helpers, "_imap_connect", lambda *a, **kw: spy)
    monkeypatch.setattr(task_endpoint, "resolve_task_candidates", lambda *a, **kw: [])

    await action_learn_sender_signatures("alice")

    assert any(c[0] == "FETCH" for c in spy.uid_calls), "uid('FETCH', ...) was not called"


@pytest.mark.asyncio
async def test_daily_brief_uses_uid_commands(monkeypatch):
    """action_daily_brief email section must use uid() not search()/fetch()."""
    from core import database
    from core import auth as _auth_mod
    from routes import email_helpers
    from src.builtin_actions import action_daily_brief

    class _Q:
        def filter(self, *a, **kw): return self
        def join(self, *a, **kw): return self
        def order_by(self, *a): return self
        def all(self): return []

    class _Db:
        def query(self, *a): return _Q()
        def close(self): pass

    class _FakeAuth:
        is_configured = False

    monkeypatch.setattr(database, "SessionLocal", _Db)
    monkeypatch.setattr(_auth_mod, "AuthManager", lambda: _FakeAuth())

    spy = _SpyImap(uid_list=b"10 20 30")
    monkeypatch.setattr(email_helpers, "_imap_connect", lambda *a, **kw: spy)

    message, ok = await action_daily_brief("")

    assert ok is True
    assert any(c[0] == "SEARCH" for c in spy.uid_calls), "uid('SEARCH', ...) was not called"
    assert any(c[0] == "FETCH" for c in spy.uid_calls), "uid('FETCH', ...) was not called"
