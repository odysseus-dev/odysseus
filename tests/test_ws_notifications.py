"""Tests for WebSocket push notification channels and API-token auth."""

import sys
import os
import types
import asyncio
import importlib
import pytest
from unittest.mock import MagicMock, patch, ANY

# ── Fixture: stub heavy modules before route imports ──────────────────────

@pytest.fixture(autouse=True)
def _ws_stubs(monkeypatch):
    """Stub core.database before routes.ws_routes is first imported."""
    _ensure_stub("core.auth", AuthManager=MagicMock())
    _ensure_stub("core.database",
        get_db_session=MagicMock(return_value=MagicMock()),
        ApiToken=MagicMock(),
        SessionLocal=MagicMock(),
    )
    _ensure_stub("core.atomic_io", atomic_write_json=MagicMock())
    _ensure_stub("routes.auth_routes", SESSION_COOKIE="odysseus_session")


def _ensure_stub(name, **attrs):
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        if parent_name not in sys.modules:
            parent = types.ModuleType(parent_name)
            real_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                *parent_name.split("."),
            )
            parent.__path__ = [real_path] if os.path.isdir(real_path) else []
            sys.modules[parent_name] = parent
        else:
            parent = sys.modules[parent_name]
    else:
        parent = None
        child_name = None
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    if parent is not None and not hasattr(parent, child_name):
        setattr(parent, child_name, mod)
    return mod


# ── Module-level pub/sub helpers ──────────────────────────────────────────

def test_subscribe_unsubscribe():
    import routes.ws_routes as ws
    importlib.reload(ws)

    q = ws._subscribe("alice")
    assert q is not None
    assert q in ws._notify_channels["alice"]

    ws._unsubscribe("alice", q)
    assert q not in ws._notify_channels.get("alice", [])


def test_push_notification_delivers():
    import routes.ws_routes as ws
    importlib.reload(ws)

    q = ws._subscribe("bob")
    note = {"task_name": "t1", "status": "success", "task_id": "id1"}
    ws.push_notification("bob", note)

    async def _pull():
        return await asyncio.wait_for(q.get(), timeout=1.0)

    assert asyncio.run(_pull()) == note


def test_push_notification_skips_unknown_owner():
    import routes.ws_routes as ws
    importlib.reload(ws)
    # Must not raise
    ws.push_notification("nonexistent", {"task_name": "t1"})


def test_push_notification_removes_full_queue():
    import routes.ws_routes as ws
    importlib.reload(ws)

    q = ws._subscribe("carol")
    for i in range(128):
        q.put_nowait({"i": i})
    assert q.full()

    ws.push_notification("carol", {"overflow": True})
    assert q not in ws._notify_channels.get("carol", [])


def test_push_notification_multiple_subscribers():
    import routes.ws_routes as ws
    importlib.reload(ws)

    q1 = ws._subscribe("dave")
    q2 = ws._subscribe("dave")
    note = {"task_name": "t2", "status": "success"}
    ws.push_notification("dave", note)

    async def _pull(q):
        return await asyncio.wait_for(q.get(), timeout=1.0)

    assert asyncio.run(_pull(q1)) == note
    assert asyncio.run(_pull(q2)) == note


# ── API token validation ─────────────────────────────────────────────────

class _FakeRow:
    """Mimics a SQLAlchemy ApiToken row for testing."""
    def __init__(self, owner="alice", token_hash="", scopes="notifications:read"):
        self.owner = owner
        self.token_hash = token_hash
        self.scopes = scopes


def _make_db_stub(rows):
    """Return a fake ``get_db_session`` context manager yielding a stub
    session whose ``query().filter().all()`` returns *rows* and
    ``query().filter().first()`` returns the first row."""
    stub_session = MagicMock()
    query_chain = MagicMock()
    query_chain.filter.return_value.all.return_value = rows
    query_chain.filter.return_value.first.return_value = rows[0] if rows else None
    stub_session.query.return_value = query_chain
    cm = MagicMock()
    cm.__enter__.return_value = stub_session
    return lambda: cm


def test_validate_api_token_valid(monkeypatch):
    """A valid ody_ token with notifications:read scope returns the owner."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"
    row = _FakeRow(owner="alice", token_hash="fake_hash", scopes="notifications:read,chat")

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([row]))
    monkeypatch.setattr(ws.bcrypt, "checkpw", lambda a, b: True)

    assert ws._validate_api_token(raw_token) == "alice"


def test_validate_api_token_missing_scope(monkeypatch):
    """A valid ody_ token without notifications:read returns None."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"
    row = _FakeRow(owner="alice", token_hash="fake_hash", scopes="chat")

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([row]))
    monkeypatch.setattr(ws.bcrypt, "checkpw", lambda a, b: True)

    assert ws._validate_api_token(raw_token) is None


def test_validate_api_token_invalid_hash(monkeypatch):
    """A token whose hash doesn't match returns None."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"
    row = _FakeRow(owner="alice", token_hash="fake_hash", scopes="notifications:read")

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([row]))
    monkeypatch.setattr(ws.bcrypt, "checkpw", lambda a, b: False)

    assert ws._validate_api_token(raw_token) is None


def test_validate_api_token_no_prefix_match(monkeypatch):
    """No matching rows in the DB returns None."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([]))

    assert ws._validate_api_token(raw_token) is None


def test_validate_api_token_short_token():
    """Tokens shorter than 12 chars are rejected outright."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    assert ws._validate_api_token("ody_short") is None


def test_validate_api_token_non_ody_prefix():
    """Tokens that don't start with ody_ are rejected."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    assert ws._validate_api_token("bearer_fake1234567890abcdef1234567890") is None


def test_validate_api_token_inactive(monkeypatch):
    """Inactive tokens are not returned by the filter, so no match."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([]))

    assert ws._validate_api_token(raw_token) is None


# ── API token re-validation ──────────────────────────────────────────────

def test_revalidate_api_token_valid(monkeypatch):
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"
    row = _FakeRow(owner="alice", scopes="notifications:read")

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([row]))
    monkeypatch.setattr(ws.bcrypt, "checkpw", lambda a, b: True)

    assert ws._revalidate_api_token(raw_token, "alice") is True


def test_revalidate_api_token_wrong_owner(monkeypatch):
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"
    row = _FakeRow(owner="bob", scopes="notifications:read")

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([row]))
    monkeypatch.setattr(ws.bcrypt, "checkpw", lambda a, b: True)

    assert ws._revalidate_api_token(raw_token, "alice") is False


def test_revalidate_api_token_revoked(monkeypatch):
    """A revoked (removed/inactive) token returns False."""
    import routes.ws_routes as ws
    importlib.reload(ws)

    raw_token = "ody_fake1234567890abcdef1234567890"

    monkeypatch.setattr(ws, "get_db_session", _make_db_stub([]))

    assert ws._revalidate_api_token(raw_token, "alice") is False
