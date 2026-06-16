"""Tests for the companion data views (notes/tasks/memory) — split 4/4.

Covers the review asks for these reads:
  - a bearer token for owner A cannot read owner B's rows (cross-owner isolation)
  - null-owner/shared rows do not widen a token's access
  - access is strictly NARROWER than chat: a plain `chat` token is rejected; only
    a token carrying the explicit `companion` scope (or a cookie session) may read
  - the endpoints are read-only (GET; no mutation verbs)
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- a tiny fake DB so we can exercise the handler's owner filtering ---------

class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *a, **k):
        return _Query(self._rows)

    def close(self):
        pass


def _install_core_database(rows, monkeypatch):
    # Use monkeypatch.setitem so the stub is restored after the test and never
    # leaks into sibling test modules (e.g. the pairing test's own core.database
    # stub that captures minted tokens).
    class _DBStub(types.ModuleType):
        def __getattr__(self, name):
            return MagicMock()
    m = _DBStub("core.database")
    m.SessionLocal = lambda: _DB(rows)
    monkeypatch.setitem(sys.modules, "core.database", m)


from companion.routes import has_companion_scope, owner_can_see, setup_companion_routes  # noqa: E402


def _req(*, api_token, current_user=None, owner=None, scopes=None):
    state = SimpleNamespace(api_token=api_token, current_user=current_user,
                            api_token_owner=owner, api_token_scopes=scopes)
    return SimpleNamespace(state=state)


# --- scope gate: strictly narrower than chat -------------------------------

def test_cookie_session_may_read():
    assert has_companion_scope(_req(api_token=False, current_user="alice")) is True


def test_companion_scoped_token_may_read():
    assert has_companion_scope(_req(api_token=True, owner="alice", scopes=["companion"])) is True
    assert has_companion_scope(_req(api_token=True, owner="alice", scopes=["chat", "companion"])) is True


def test_plain_chat_token_is_too_narrow_to_read():
    # The whole point: a chat token cannot read your private notes/memory.
    assert has_companion_scope(_req(api_token=True, owner="alice", scopes=["chat"])) is False


def test_token_without_scopes_cannot_read():
    assert has_companion_scope(_req(api_token=True, owner="alice", scopes=None)) is False
    assert has_companion_scope(_req(api_token=True, owner="alice", scopes=[])) is False


# --- owner-scope rule (shared by all three views) --------------------------

def test_cross_owner_blocked_and_null_owner_shared():
    assert owner_can_see("alice", "alice") is True
    assert owner_can_see(None, "alice") is True       # shared row visible
    assert owner_can_see("bob", "alice") is False      # cross-owner blocked
    assert owner_can_see("alice", None) is False        # null caller sees no owned row


# --- handler-level: /notes filters out another owner's rows ----------------

def _notes_handler():
    router = setup_companion_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/notes"):
            return r.endpoint
    raise AssertionError("/notes route not found")


def test_notes_handler_excludes_other_owners_rows(monkeypatch):
    # The SQL filter is the first line of defence; this proves the in-Python
    # owner_can_see check also drops a cross-owner row that slipped through.
    rows = [
        SimpleNamespace(id="n1", owner="alice", title="mine", content="x", items=None, pinned=False, archived=False),
        SimpleNamespace(id="n2", owner="bob", title="theirs", content="y", items=None, pinned=False, archived=False),
        SimpleNamespace(id="n3", owner=None, title="shared", content="z", items=None, pinned=True, archived=False),
    ]
    _install_core_database(rows, monkeypatch)
    handler = _notes_handler()
    req = _req(api_token=True, owner="alice", scopes=["companion"])
    result = handler(req)
    ids = {n["id"] for n in result["items"]}
    assert ids == {"n1", "n3"}          # alice's + shared, never bob's
    assert "n2" not in ids


def test_notes_handler_rejects_chat_only_token(monkeypatch):
    from fastapi import HTTPException
    import pytest
    _install_core_database([], monkeypatch)
    handler = _notes_handler()
    req = _req(api_token=True, owner="alice", scopes=["chat"])
    with pytest.raises(HTTPException) as exc:
        handler(req)
    assert exc.value.status_code == 403


# --- read-only: the data views are GET, no mutation verbs ------------------

def test_data_endpoints_are_read_only():
    router = setup_companion_routes()
    for path_suffix in ("/notes", "/tasks", "/memory"):
        methods = set()
        for r in router.routes:
            if getattr(r, "path", "").endswith(path_suffix):
                methods |= set(getattr(r, "methods", set()) or set())
        assert methods == {"GET"}, f"{path_suffix} must be GET-only, got {methods}"
