"""Tests for the per-session asyncio.Lock that serializes history mutations.

Covers:
1. session_lock identity — same object for same session_id, different for different.
2. session_lock returns an asyncio.Lock.
3. Concurrent compact_session vs. stream add_message does not lose writes when both
   sides acquire session_lock — the stream side mirrors what save_assistant_response
   does (acquire session_lock, then call sess.add_message).

Import pattern mirrors tests/test_session_ghost_delete.py: temporarily stub the
heavy ORM modules so SessionManager can be imported without a live database.
"""

import sys
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy DB/ORM deps so SessionManager can be imported in isolation.
# We RESTORE sys.modules after import so the stubs never leak into siblings.
# ---------------------------------------------------------------------------
_ABSENT = object()
_TEMP_STUBS = ("core.database", "core.models", "src.request_models")
_saved = {name: sys.modules.get(name, _ABSENT) for name in _TEMP_STUBS}
_saved["core.session_manager"] = sys.modules.get("core.session_manager", _ABSENT)
try:
    for _name in _TEMP_STUBS:
        sys.modules[_name] = MagicMock(name=_name)
    if isinstance(sys.modules.get("core.session_manager"), MagicMock):
        del sys.modules["core.session_manager"]
    SM = importlib.import_module("core.session_manager")
finally:
    for _name, _val in _saved.items():
        if _val is _ABSENT:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _val


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bare_manager():
    """Return a SessionManager without hitting the database."""
    mgr = SM.SessionManager.__new__(SM.SessionManager)
    mgr.sessions = {}
    mgr._locks = {}
    return mgr


class _FakeMessage:
    """Minimal stand-in for ChatMessage — only needs .content."""
    def __init__(self, role, content):
        self.role = role
        self.content = content
        self.metadata = {}


# ---------------------------------------------------------------------------
# Lock identity tests (sync — no event loop needed)
# ---------------------------------------------------------------------------

def test_session_lock_same_session_id_returns_same_lock():
    """session_lock must hand back the identical lock object on repeated calls."""
    mgr = _bare_manager()
    lock1 = mgr.session_lock("abc-123")
    lock2 = mgr.session_lock("abc-123")
    assert lock1 is lock2


def test_session_lock_different_session_ids_return_different_locks():
    """Each session_id must get its own independent lock."""
    mgr = _bare_manager()
    lock_a = mgr.session_lock("session-A")
    lock_b = mgr.session_lock("session-B")
    assert lock_a is not lock_b


def test_session_lock_returns_asyncio_lock():
    """The returned object must be an asyncio.Lock (usable with ``async with``)."""
    mgr = _bare_manager()
    lock = mgr.session_lock("some-id")
    assert isinstance(lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# Concurrency test — both sides of the race acquire the same lock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_compact_and_stream_do_not_lose_writes():
    """Simulate the compact_session race: one coroutine holds the session lock
    across a read→replace window (mimicking compact_session), while another
    coroutine holds the same lock to append a message (mimicking the stream
    writer path that save_assistant_response now uses).

    After both complete:
    - The compacted summary must be present (compact_path write not lost).
    - The stream reply must be present (stream write not lost).
    - Total message count must be len(compacted) + 1.
    """
    mgr = _bare_manager()
    session_id = "race-test"

    original_messages = [_FakeMessage("user", f"msg{i}") for i in range(10)]
    session = SimpleNamespace(
        history=list(original_messages),
        message_count=len(original_messages),
    )
    mgr.sessions[session_id] = session

    compacted = [_FakeMessage("system", "[summary]")] + original_messages[-2:]
    stream_msg = _FakeMessage("assistant", "stream reply")
    results = {}

    async def compact_path():
        """Mimics compact_session: acquires lock, snapshots history, replaces.
        This is the existing behaviour from session_routes.py."""
        async with mgr.session_lock(session_id):
            _ = list(mgr.sessions[session_id].history)
            # Yield to event loop — lets stream_path contend for the lock.
            await asyncio.sleep(0)
            mgr.sessions[session_id].history = list(compacted)
            mgr.sessions[session_id].message_count = len(compacted)
        results["compact_done"] = True

    async def stream_path():
        """Mimics save_assistant_response: acquires session_lock, then appends.
        This mirrors the real code path added to routes/chat_helpers.py."""
        async with mgr.session_lock(session_id):
            mgr.sessions[session_id].history.append(stream_msg)
            mgr.sessions[session_id].message_count += 1
        results["stream_done"] = True

    await asyncio.gather(compact_path(), stream_path())

    final = mgr.sessions[session_id].history
    assert results.get("compact_done") and results.get("stream_done"), \
        "Both coroutines must complete"

    assert any(m.content == "[summary]" for m in final), \
        "Compacted summary message was lost from history"

    assert any(m.content == "stream reply" for m in final), \
        "Concurrent stream message was lost from history"

    assert len(final) == len(compacted) + 1, (
        f"Expected {len(compacted) + 1} messages, got {len(final)}"
    )
