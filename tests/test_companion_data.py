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

import pytest
from fastapi import HTTPException

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


# --- owner-scope rule --------------------------------------------------------
# owner_can_see is the generic read predicate used by the shareable views
# (documents/gallery) where legacy null-owner rows are intentionally visible.
# The PRIVATE companion views (notes/tasks/memory) do NOT use it — they filter
# by EXACT owner (see read_owner + the handler tests below).

def test_owner_can_see_predicate():
    assert owner_can_see("alice", "alice") is True
    assert owner_can_see(None, "alice") is True       # shared row visible
    assert owner_can_see("bob", "alice") is False      # cross-owner blocked
    assert owner_can_see("alice", None) is False        # null caller sees no owned row


# --- handler-level: /notes is strictly owner-scoped ------------------------

def _notes_handler():
    router = setup_companion_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/notes"):
            return r.endpoint
    raise AssertionError("/notes route not found")


_NOTE_ROWS = [
    SimpleNamespace(id="n1", owner="alice", title="mine", content="x", items=None, pinned=False, archived=False),
    SimpleNamespace(id="n2", owner="bob", title="theirs", content="y", items=None, pinned=False, archived=False),
    SimpleNamespace(id="n3", owner=None, title="ownerless", content="z", items=None, pinned=True, archived=False),
]


def test_notes_handler_excludes_other_and_null_owner_rows(monkeypatch):
    # An authenticated caller sees ONLY their own rows: never another owner's,
    # and never a legacy null-owner row (which could carry residual private
    # content). The fake query ignores SQL filters, so this pins the in-Python
    # exact-owner guard that backs up the SQL WHERE.
    _install_core_database(list(_NOTE_ROWS), monkeypatch)
    req = _req(api_token=True, owner="alice", scopes=["companion"])
    result = _notes_handler()(req)
    ids = {n["id"] for n in result["items"]}
    assert ids == {"n1"}                 # alice's only — not bob's, not the null-owner row


def test_notes_handler_fails_closed_without_owner_when_auth_on(monkeypatch):
    # Auth is ON (default) but no owner resolved → reject, rather than fall back
    # to single-user mode and expose every account's private notes.
    _install_core_database(list(_NOTE_ROWS), monkeypatch)
    req = _req(api_token=True, owner=None, scopes=["companion"])
    with pytest.raises(HTTPException) as exc:
        _notes_handler()(req)
    assert exc.value.status_code == 403


def test_notes_handler_single_user_mode_shows_local_rows(monkeypatch):
    # AUTH_ENABLED=false: owner is None by design and the owning routes skip the
    # owner filter, so the companion view must show the local user's rows too
    # (not silently empty). Mirrors routes/note_routes.list_notes.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _install_core_database(list(_NOTE_ROWS), monkeypatch)
    req = _req(api_token=False, current_user=None)
    result = _notes_handler()(req)
    ids = {n["id"] for n in result["items"]}
    assert ids == {"n1", "n2", "n3"}     # single-user: no owner boundary, show everything


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


# --- end-to-end: the MINTED pairing scope matches this consumer -------------
# Ties the scope GRANT (pairing mints `companion`) to the GATE in this PR. The
# pairing flow mints `COMPANION_SCOPE`; a real paired phone must both (a) clear
# the companion data gate AND (b) keep chat streaming, which requires the
# "chat" scope. Reviewing the grant + consumer + these tests in one window is
# exactly what the maintainer asked for before widening the paired scope.

import companion.pairing as _pairing  # noqa: E402


def _paired_scopes():
    """The scope list a freshly paired device actually carries."""
    return [s.strip() for s in _pairing.COMPANION_SCOPE.split(",") if s.strip()]


def test_paired_scope_is_exactly_chat_and_companion():
    # Guards against silently dropping/adding a scope on the pairing path.
    assert set(_paired_scopes()) == {"chat", "companion"}


def test_paired_token_keeps_chat_so_streaming_still_works():
    # Chat streaming gates on the "chat" scope; widening to companion must not
    # drop it, or every paired phone would lose chat.
    assert "chat" in _paired_scopes()


def test_paired_token_clears_the_companion_data_gate():
    req = _req(api_token=True, owner="alice", scopes=_paired_scopes())
    assert has_companion_scope(req) is True


def _data_handler(path_suffix):
    router = setup_companion_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith(path_suffix):
            return r.endpoint
    raise AssertionError(f"{path_suffix} route not found")


@pytest.mark.parametrize("suffix", ["/notes", "/tasks", "/memory"])
def test_all_data_routes_reject_chat_only_token(suffix, monkeypatch):
    # A plain chat token (no companion scope) must be rejected on ALL three
    # data views, not just /notes.
    _install_core_database([], monkeypatch)
    req = _req(api_token=True, owner="alice", scopes=["chat"])
    with pytest.raises(HTTPException) as exc:
        _data_handler(suffix)(req)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("suffix", ["/notes", "/tasks", "/memory"])
def test_all_data_routes_accept_paired_token(suffix, monkeypatch):
    # The exact scope the pairing flow mints is accepted on all three views
    # (no 403); same owner sees a well-formed (here empty) result.
    _install_core_database([], monkeypatch)
    req = _req(api_token=True, owner="alice", scopes=_paired_scopes())
    result = _data_handler(suffix)(req)
    assert isinstance(result, dict) and "items" in result


# --- memory reads through the active MemoryManager, not the ORM table -------

class _FakeMemMgr:
    """Records load() calls; filters like the real MemoryManager.load(owner=...)."""

    def __init__(self, entries):
        self._entries = entries
        self.calls = []

    def load(self, owner=None):
        self.calls.append(owner)
        if owner is None:
            return list(self._entries)
        return [e for e in self._entries if e.get("owner") == owner]


def _memory_handler(mm):
    router = setup_companion_routes(memory_manager=mm)
    for r in router.routes:
        if getattr(r, "path", "").endswith("/memory"):
            return r.endpoint
    raise AssertionError("/memory route not found")


def test_memory_reads_through_memory_manager_owner_scoped():
    # The app persists memories via MemoryManager (memory.json), NOT the ORM
    # Memory table — so the companion view must read the manager, exact-owner.
    mm = _FakeMemMgr([
        {"id": "m1", "text": "alice's", "category": "fact", "owner": "alice"},
        {"id": "m2", "text": "bob's", "category": "fact", "owner": "bob"},
    ])
    req = _req(api_token=True, owner="alice", scopes=["companion"])
    result = _memory_handler(mm)(req)
    assert {m["id"] for m in result["items"]} == {"m1"}   # alice's only
    assert mm.calls == ["alice"]                           # loaded exact-owner


def test_memory_single_user_mode_loads_all(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    mm = _FakeMemMgr([
        {"id": "m1", "text": "x", "category": "fact", "owner": "alice"},
        {"id": "m2", "text": "y", "category": "fact", "owner": None},
    ])
    req = _req(api_token=False, current_user=None)
    result = _memory_handler(mm)(req)
    assert {m["id"] for m in result["items"]} == {"m1", "m2"}
    assert mm.calls == [None]                              # unfiltered single-user load


# --- P2: the companion scope round-trips through token management -----------

def test_companion_scope_round_trips_in_token_management():
    # Editing a paired token's permissions PATCHes the whole scope list; if
    # `companion` weren't an allowed scope it would 400 (or be dropped), silently
    # revoking the paired phone's notes/tasks/memory access.
    from routes.api_token_routes import ALLOWED_SCOPES, _normalize_scopes

    assert "companion" in ALLOWED_SCOPES
    assert set(_normalize_scopes(["chat", "companion"])) == {"chat", "companion"}
    assert set(_normalize_scopes("chat,companion")) == {"chat", "companion"}
