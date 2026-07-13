"""Regression guard for PR #4661 — history pagination contract.

`routes/history/history_routes.py` paginates session history with `?limit=k` and
`?offset=j`.  The frontend pager (`sessions.js`, `chat.js`) relies on two
invariants:

  1. An initial `?limit=k` request (no explicit `offset`) returns the
     **most-recent** `k` chronological messages, not the oldest.
  2. Explicit offsets (`?limit=k&offset=j`) page backward from the
     most-recent window without gaps or duplicates when driven by the
     frontend's `_loadOlderMessages()` offset math.

This test pins those invariants against the canonical FastAPI endpoint.
Limit-based tests use a temp file-backed SQLite DB via monkeypatching
``core.database.SessionLocal``; no-limit tests exercise the in-memory
fallback path via a fake session manager.
"""

import json
from datetime import datetime, timezone

import core.database
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.history_routes as history_routes
from core.models import ChatMessage
from core.database import (
    Session as DbSession,
    ChatMessage as DbChatMessage,
)
from tests.helpers.sqlite_db import make_temp_sqlite


# ---------------------------------------------------------------------------
# Fake session infrastructure (same pattern as test_history_compact_tool_calls)
# ---------------------------------------------------------------------------

class _FakeSession:
    owner = "test-owner"
    headers = {}

    def __init__(self, history):
        self.history = list(history)
        self.message_count = len(history)
        self.name = "pagination-test"
        self.model = "test-model"
        self.endpoint_url = "http://example.test/v1"

    def get_context_messages(self):
        return [
            msg.to_dict() if isinstance(msg, ChatMessage) else msg
            for msg in self.history
        ]


class _FakeSessionManager:
    def __init__(self, session):
        self.session = session

    def get_session(self, session_id):
        if session_id != self.session.id:
            raise KeyError(session_id)
        return self.session


def _build_app(monkeypatch, session):
    """Build a FastAPI app with the history routes wired to a fake session."""
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda request, session_id: None)

    manager = _FakeSessionManager(session)
    app = FastAPI()
    app.include_router(history_routes.setup_history_routes(manager))
    return manager, TestClient(app)


def _make_messages(count):
    """Build `count` chronological ChatMessages with distinct content."""
    return [
        ChatMessage(role="user" if i % 2 == 0 else "assistant",
                    content=f"message-{i}")
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# DB seeding — uses the same temp SessionLocal that the canonical endpoint
# will use, because monkeypatch sets it on core.database.SessionLocal.
# ---------------------------------------------------------------------------

def _seed_db(session_id, messages, SessionLocal):
    """Insert a Session row and ChatMessage rows into the temp DB."""
    db = SessionLocal()
    try:
        s = DbSession(
            id=session_id,
            name="pagination-test",
            model="test-model",
            endpoint_url="http://example.test/v1",
            owner="test-owner",
        )
        db.add(s)

        for i, msg in enumerate(messages):
            role = msg.role if isinstance(msg, ChatMessage) else msg.get("role", "user")
            content = msg.content if isinstance(msg, ChatMessage) else msg.get("content", "")
            metadata = msg.metadata if isinstance(msg, ChatMessage) else msg.get("metadata")
            meta_json = json.dumps(metadata) if metadata else None
            db.add(DbChatMessage(
                id=f"msg-{session_id}-{i}",
                session_id=session_id,
                role=role,
                content=content,
                meta_data=meta_json,
                timestamp=datetime.now(timezone.utc),
            ))
        db.commit()
    finally:
        db.close()


def _setup_temp_db(monkeypatch):
    """Create a temp file-backed SQLite DB and monkeypatch SessionLocal.

    The canonical module (routes/history/history_routes.py) imports
    ``SessionLocal`` from ``core.database`` at module level.  Patching
    ``core.database.SessionLocal`` works for fresh imports but the
    canonical module already holds a local reference.  We patch BOTH
    ``core.database.SessionLocal`` AND the canonical module's attribute
    (which, via the shim, is ``history_routes.SessionLocal``).

    Returns the temp SessionLocal. The caller must keep the returned
    (SessionLocal, engine, tmpfile) tuple alive for the test duration.
    """
    SessionLocal, engine, tmpfile = make_temp_sqlite(core.database.Base.metadata)
    monkeypatch.setattr(core.database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(history_routes, "SessionLocal", SessionLocal)
    return SessionLocal, engine, tmpfile


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_initial_request_returns_most_recent_messages(monkeypatch):
    """`?limit=k` with no explicit offset returns the k most-recent messages."""
    SessionLocal, _engine, _tmp = _setup_temp_db(monkeypatch)
    messages = _make_messages(10)  # msg-0 … msg-9 (chronological)
    _seed_db("s1", messages, SessionLocal)

    session = _FakeSession(messages)
    session.id = "s1"
    manager, client = _build_app(monkeypatch, session)

    resp = client.get("/api/history/s1?limit=4")
    assert resp.status_code == 200
    body = resp.json()

    history = body["history"]

    # Should return the 4 most-recent messages.
    assert len(history) == 4
    assert [m["content"] for m in history] == [
        "message-6", "message-7", "message-8", "message-9",
    ]

    # `total` must reflect the full (non-hidden) count so the frontend
    # can compute backward offsets.
    assert body["total"] == 10


def test_explicit_offset_returns_correct_slice(monkeypatch):
    """`?limit=k&offset=j` returns the exact slice from the chronological array."""
    SessionLocal, _engine, _tmp = _setup_temp_db(monkeypatch)
    messages = _make_messages(10)
    _seed_db("s2", messages, SessionLocal)

    session = _FakeSession(messages)
    session.id = "s2"
    manager, client = _build_app(monkeypatch, session)

    # offset=3, limit=4 → messages[3:7]
    resp = client.get("/api/history/s2?limit=4&offset=3")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["history"]) == 4
    assert [m["content"] for m in body["history"]] == [
        "message-3", "message-4", "message-5", "message-6",
    ]
    assert body["total"] == 10


def test_backward_paging_no_gaps_or_duplicates(monkeypatch):
    """Adjacent explicit-offset windows must not overlap or leave gaps.

    The frontend's `_loadOlderMessages()` walks backward from the most-recent
    window using explicit offsets.  Two back-to-back `?limit=k&offset=j` and
    `?limit=k&offset=j+k` requests must return disjoint, contiguous slices
    that together cover exactly the full range without duplicates.
    """
    SessionLocal, _engine, _tmp = _setup_temp_db(monkeypatch)
    messages = _make_messages(10)  # msg-0 … msg-9
    _seed_db("s3", messages, SessionLocal)

    session = _FakeSession(messages)
    session.id = "s3"
    manager, client = _build_app(monkeypatch, session)

    # Verify the full set can be walked via explicit offsets with no overlap.
    limit = 3
    all_loaded = []
    for offset in range(0, len(messages), limit):
        resp = client.get(f"/api/history/s3?limit={limit}&offset={offset}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 10
        window = body["history"]

        # Each window must contribute only new messages.
        contents = [m["content"] for m in window]
        already_seen = set(m["content"] for m in all_loaded)
        assert not already_seen.intersection(contents), (
            f"Offset window at offset={offset} overlaps with earlier "
            f"windows: {contents} ∩ {already_seen}"
        )
        all_loaded.extend(window)

    # After walking the full array, we must have every message exactly once.
    loaded_contents = sorted(m["content"] for m in all_loaded)
    expected_contents = sorted(f"message-{i}" for i in range(10))
    assert loaded_contents == expected_contents, (
        f"Explicit-offset walk missed or duplicated messages.\n"
        f"Loaded ({len(loaded_contents)}): {loaded_contents}\n"
        f"Expected ({len(expected_contents)}): {expected_contents}"
    )


def test_most_recent_then_explicit_offset_backward(monkeypatch):
    """No-offset most-recent window followed by explicit-offset older windows.

    This directly exercises the frontend flow: an initial `?limit=k` loads
    the most-recent messages (no explicit offset), then the caller pages
    backward via explicit offsets computed from `total`.  The two windows
    must be adjacent — no overlap and no gap.
    """
    SessionLocal, _engine, _tmp = _setup_temp_db(monkeypatch)
    messages = _make_messages(10)
    _seed_db("s3b", messages, SessionLocal)

    session = _FakeSession(messages)
    session.id = "s3b"
    manager, client = _build_app(monkeypatch, session)

    limit = 3

    # Step 1: initial request → most-recent `limit` messages.
    resp = client.get(f"/api/history/s3b?limit={limit}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    recent = [m["content"] for m in body["history"]]
    assert recent == ["message-7", "message-8", "message-9"]

    # Step 2: offset to get the immediately-prior window (no gap).
    # start of most-recent = total - limit = 10 - 3 = 7
    # prior offset = 7 - limit = 4
    resp2 = client.get(f"/api/history/s3b?limit={limit}&offset=4")
    assert resp2.status_code == 200
    prior = [m["content"] for m in resp2.json()["history"]]
    assert prior == ["message-4", "message-5", "message-6"]

    # No overlap between the two windows.
    assert set(recent).isdisjoint(set(prior)), (
        f"Most-recent window {recent} overlaps with prior window {prior}"
    )

    # No gap: the union should be exactly indices 4–9.
    combined = sorted(recent + prior)
    expected = sorted(f"message-{i}" for i in range(4, 10))
    assert combined == expected, (
        f"Gap between most-recent and prior windows.\n"
        f"Got: {combined}\nExpected: {expected}"
    )


def test_total_excludes_hidden_messages(monkeypatch):
    """`total` must exclude hidden messages so offset math stays consistent."""
    messages = _make_messages(5)
    # Mark msg-2 and msg-4 as hidden (compaction summaries).
    hidden_meta = {"hidden": True}
    messages[2] = ChatMessage(role="user", content="message-2", metadata=hidden_meta)
    messages[4] = ChatMessage(role="assistant", content="message-4", metadata=hidden_meta)

    session = _FakeSession(messages)
    session.id = "s4"
    manager, client = _build_app(monkeypatch, session)

    resp = client.get("/api/history/s4")
    assert resp.status_code == 200
    body = resp.json()

    # 5 total messages, 2 hidden → total should be 3.
    assert body["total"] == 3
    contents = [m["content"] for m in body["history"]]
    assert "message-2" not in contents
    assert "message-4" not in contents
    assert contents == ["message-0", "message-1", "message-3"]


def test_no_limit_returns_all_non_hidden_messages(monkeypatch):
    """Without `?limit`, every non-hidden message is returned."""
    messages = _make_messages(5)
    session = _FakeSession(messages)
    session.id = "s5"
    manager, client = _build_app(monkeypatch, session)

    resp = client.get("/api/history/s5")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["history"]) == 5
    assert body["total"] == 5
