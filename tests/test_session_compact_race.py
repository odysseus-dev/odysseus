"""Tests for the per-session asyncio.Lock that serializes history mutations.

Covers:
1. session_lock identity — same object for same session_id, different for different.
2. session_lock returns an asyncio.Lock.
3. Concurrent compact_session vs. a REAL add_message call does not lose writes
   when both sides acquire session_lock — the "stream" side now goes through
   SessionManager.add_message itself (the actual method routes/chat_helpers.py
   and routes/chat_routes.py call for every assistant/user turn), wrapped in
   `async with session_lock(...)` exactly as save_assistant_response and
   add_user_message do in production. This exercises the real contended code
   path instead of a manually-locked stand-in.
4. A regression guard showing the race is real: if add_message runs WITHOUT
   acquiring session_lock while compact_session holds it across its
   read→replace window, the concurrent write is lost — demonstrating exactly
   the interleaving #2654 describes, and why both contending paths must take
   the same lock for the fix to actually close the race.

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

class _FakeMessage:
    """Minimal stand-in for ChatMessage — only needs .role/.content/.metadata,
    which is everything add_message / _persist_message touch on the in-memory
    side."""
    def __init__(self, role, content):
        self.role = role
        self.content = content
        self.metadata = {}


def _bare_manager():
    """Return a SessionManager wired up so add_message's REAL body runs against
    an in-memory session, with only its DB side effects stubbed out.

    add_message does three things: (1) self.get_session(session_id) — which for
    an already-cached session calls sync_session_metadata + _touch_session
    (both pure DB I/O), (2) session.history.append(...) — the in-memory
    mutation we care about, and (3) self._persist_message(...) — a DB write.
    We no-op (1)'s DB calls and (3) so the test exercises the exact in-memory
    contention add_message has in production without needing a live database.
    """
    mgr = SM.SessionManager.__new__(SM.SessionManager)
    mgr.sessions = {}
    mgr._locks = {}
    mgr.sync_session_metadata = MagicMock(return_value=True)
    mgr._touch_session = MagicMock()
    mgr._persist_message = MagicMock()
    return mgr


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
# Concurrency tests — both sides of the race
# ---------------------------------------------------------------------------

def _seed_session(mgr, session_id, n=10):
    original_messages = [_FakeMessage("user", f"msg{i}") for i in range(n)]
    session = SimpleNamespace(
        history=list(original_messages),
        message_count=len(original_messages),
    )
    mgr.sessions[session_id] = session
    return session, original_messages


async def _compact_path(mgr, session_id, compacted, results):
    """Mimics compact_session: snapshot → (yield) → replace, across a window
    held under session_lock — the actual fixed behaviour in session_routes.py."""
    async with mgr.session_lock(session_id):
        _ = list(mgr.sessions[session_id].history)
        # Yield to event loop — lets the other side contend for the lock /
        # interleave its append into the in-memory list.
        await asyncio.sleep(0)
        mgr.sessions[session_id].history = list(compacted)
        mgr.sessions[session_id].message_count = len(compacted)
    results["compact_done"] = True


async def _compact_steps(mgr, session_id, compacted, results):
    """Same read→replace window as compact_session, exposed as an async
    generator with explicit yield points so a test can deterministically
    interleave another coroutine's work between the snapshot and the replace —
    without relying on event-loop scheduling order (which `asyncio.gather` +
    `sleep(0)` doesn't guarantee either way)."""
    _ = list(mgr.sessions[session_id].history)
    yield  # <- caller resumes here: snapshot done, replace not yet applied
    mgr.sessions[session_id].history = list(compacted)
    mgr.sessions[session_id].message_count = len(compacted)
    results["compact_done"] = True
    yield


@pytest.mark.asyncio
async def test_concurrent_compact_and_add_message_do_not_lose_writes():
    """Both racing paths take session_lock — the actual fix for #2654.

    One coroutine mimics compact_session (snapshot → yield → replace, holding
    session_lock across the whole window). The other calls the REAL
    SessionManager.add_message — the exact method every streaming write site
    in routes/chat_helpers.py and routes/chat_routes.py calls — wrapped in
    `async with session_lock(...)` exactly as save_assistant_response and
    add_user_message do in production.

    After both complete, neither write may be lost.
    """
    mgr = _bare_manager()
    session_id = "race-test"
    session, original_messages = _seed_session(mgr, session_id)

    compacted = [_FakeMessage("system", "[summary]")] + original_messages[-2:]
    stream_msg = _FakeMessage("assistant", "stream reply")
    results = {}

    async def add_message_path():
        """Mirrors the real call sites: acquire session_lock, then call the
        unmodified SessionManager.add_message — not a stand-in."""
        async with mgr.session_lock(session_id):
            mgr.add_message(session_id, stream_msg)
        results["stream_done"] = True

    await asyncio.gather(
        _compact_path(mgr, session_id, compacted, results),
        add_message_path(),
    )

    final = mgr.sessions[session_id].history
    assert results.get("compact_done") and results.get("stream_done"), \
        "Both coroutines must complete"

    assert any(m.content == "[summary]" for m in final), \
        "Compacted summary message was lost from history"

    assert any(m.content == "stream reply" for m in final), \
        "Concurrent add_message write was lost from history — the lock did not " \
        "actually serialize the two contending paths"

    assert len(final) == len(compacted) + 1, (
        f"Expected {len(compacted) + 1} messages, got {len(final)}"
    )


@pytest.mark.asyncio
async def test_unsynchronized_add_message_loses_writes_during_compact_replace():
    """Regression guard / race reproduction: if add_message runs WITHOUT
    session_lock while compact_session holds it across the read→replace
    window, the interleaved write is lost.

    This is the exact bug #2654 reports, and the reason the fix must make
    BOTH `compact_session` and `add_message`'s callers take session_lock —
    a lock held by only one side cannot prevent the race. If this test ever
    starts failing (i.e. the write stops being lost) it likely means
    add_message itself started taking the lock internally, which would make
    the `async with session_lock(...)` wrapping at the call sites redundant
    (harmless, but worth revisiting this test+the call sites together).
    """
    mgr = _bare_manager()
    session_id = "race-test-unsynced"
    session, original_messages = _seed_session(mgr, session_id)

    compacted = [_FakeMessage("system", "[summary]")] + original_messages[-2:]
    stream_msg = _FakeMessage("assistant", "stream reply")
    results = {}

    async def unsynced_add_message_path():
        """The bug: calls add_message directly, with no lock — exactly what
        the pre-fix streaming call sites did. add_message itself is sync and
        does not yield, so this lands its append on whatever list object
        `history` currently points to the instant it runs."""
        mgr.add_message(session_id, stream_msg)
        results["stream_done"] = True

    async def driver():
        """Interleave deterministically: let compact_session take its snapshot
        (the read half of its read→replace window), run the unsynchronized
        add_message so it appends into the OLD list compact_session already
        snapshotted-past, then let compact_session finish its replace — which
        #2654 says clobbers the concurrent write."""
        gen = _compact_steps(mgr, session_id, compacted, results)
        await gen.__anext__()  # advance to just after the snapshot
        await unsynced_add_message_path()
        async for _ in gen:  # drain — runs the replace
            pass

    await driver()

    final = mgr.sessions[session_id].history
    assert results.get("compact_done") and results.get("stream_done")

    # The whole point: prove the unsynchronized write gets clobbered by
    # compact_session's `history = list(compacted)` replace.
    assert not any(m.content == "stream reply" for m in final), (
        "Expected the unsynchronized add_message write to be lost (demonstrating "
        "the #2654 race) — but it survived, so this reproduction no longer "
        "demonstrates the bug the lock is meant to fix."
    )
