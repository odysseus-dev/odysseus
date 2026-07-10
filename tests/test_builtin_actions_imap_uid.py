"""Issue #5148 — scheduled email actions must fetch by UID, not sequence number.

The Learn Sender Signatures and Daily Brief scheduled actions used
``conn.search(...)`` / ``conn.fetch(...)`` which operate on IMAP *sequence
numbers*. Sequence numbers are positional and shift when earlier messages are
deleted/expunged, so after a mailbox deletion these actions fetched the *wrong*
message (or hit NO). IMAP UIDs are stable across such changes, so the fix is to
use ``conn.uid("SEARCH", ...)`` / ``conn.uid("FETCH", ...)`` — the same pattern
the sibling urgency check (``action_check_email_urgency``) already uses.

These tests inject a recording IMAP stub and assert the UID command path is
taken (and the bare sequence-number ``search``/``fetch`` methods are never
called) for both scheduled actions.
"""

import asyncio

import pytest


class _RecordingImap:
    """Records command names so the test can assert UID vs sequence usage."""

    def __init__(self):
        self.calls = []  # ordered list of ("uid", subcommand, args...) / ("search", ...) / ("fetch", ...)

    def select(self, *_args, **_kwargs):
        return "OK", []

    def search(self, *args):
        self.calls.append(("search", *args))
        return "OK", [b""]

    def fetch(self, *args):
        self.calls.append(("fetch", *args))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command.upper() == "SEARCH":
            return "OK", [b"1 2"]
        # FETCH — return a minimal header tuple the callers can parse.
        return "OK", [(b"1 (UID 1 BODY[HEADER.FIELDS (FROM SUBJECT)])",
                       b"From: a@example.com\r\nSubject: Hi\r\n\r\n")]

    def logout(self):
        return "OK", []


@pytest.fixture
def _recording_imap(monkeypatch):
    """Patch every IMAP entrypoint builtin_actions reaches through."""
    import routes.email_helpers as email_helpers

    imap = _RecordingImap()

    def fake_connect(_account_id=None, owner="", timeout=None):
        return imap

    # action_daily_brief / action_learn_sender_signatures import _imap_connect
    # from routes.email_helpers at call time, so patching the module attribute
    # is enough.
    monkeypatch.setattr(email_helpers, "_imap_connect", fake_connect)
    return imap


def _patch_empty_db(monkeypatch):
    """Make SessionLocal().query(...) return empty lists for every model."""
    import core.database as db_mod

    class _Chain:
        def join(self, *_a, **_k):
            return self

        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def all(self):
            return []

    class _FakeDb:
        def query(self, _model):
            return _Chain()

        def close(self):
            pass

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeDb())


def test_daily_brief_fetches_unseen_by_uid(monkeypatch, _recording_imap):
    import src.builtin_actions as builtin_actions

    _patch_empty_db(monkeypatch)

    asyncio.run(builtin_actions.action_daily_brief(owner="alice"))

    subcommands = [c for c in _recording_imap.calls if c[0] == "uid"]
    # Must issue a UID SEARCH (for UNSEEN) and UID FETCH (for headers).
    assert any(c[1].upper() == "SEARCH" for c in subcommands), subcommands
    assert any(c[1].upper() == "FETCH" for c in subcommands), subcommands
    # And must never fall back to the sequence-number API.
    assert not any(c[0] == "search" for c in _recording_imap.calls)
    assert not any(c[0] == "fetch" for c in _recording_imap.calls)


def test_learn_sender_signatures_searches_all_by_uid(monkeypatch, _recording_imap):
    """Issue #5148 — sender-signature header pull must UID SEARCH ALL.

    This action short-circuits when there are <3 messages or no LLM endpoint,
    so we only assert the IMAP *search* contract (UID SEARCH ALL, never bare
    sequence-number search) rather than the full LLM pipeline.
    """
    import src.builtin_actions as builtin_actions

    _patch_empty_db(monkeypatch)
    # No LLM endpoint → the action returns early, but only after the header
    # pull (which is where the sequence-vs-UID bug lived) has run.
    monkeypatch.setattr(
        builtin_actions, "resolve_task_candidates",
        lambda owner=None: [], raising=False,
    )
    # Ensure the early-skip branches (already-cached, <3 senders) don't fire
    # before the header pull: the stub returns 2 messages from one sender,
    # which is below the >=3 threshold, so the action exits after pulling
    # headers — exactly the path we want to assert on.

    asyncio.run(builtin_actions.action_learn_sender_signatures(owner="alice"))

    # The header pull must use UID SEARCH ALL.
    assert any(
        c[0] == "uid" and c[1].upper() == "SEARCH" and "ALL" in c[2:]
        for c in _recording_imap.calls
    ), _recording_imap.calls
    assert not any(c[0] == "search" for c in _recording_imap.calls)
