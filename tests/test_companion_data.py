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


from companion.routes import (  # noqa: E402
    has_companion_scope,
    owner_can_see,
    require_companion_scope,
    setup_companion_routes,
    writer_owner,
)


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


# --- route surface: tasks stays read-only; notes/memory gain writes --------

def _methods_for(router, path_suffix):
    methods = set()
    for r in router.routes:
        if getattr(r, "path", "").endswith(path_suffix):
            methods |= set(getattr(r, "methods", set()) or set())
    return methods


def test_tasks_stays_read_only():
    # Tasks have no mobile write affordance; keep the surface minimal.
    assert _methods_for(setup_companion_routes(), "/tasks") == {"GET"}


def test_notes_and_memory_expose_writes():
    router = setup_companion_routes()
    assert _methods_for(router, "/notes") == {"GET", "POST"}
    assert _methods_for(router, "/notes/{note_id}") == {"DELETE"}
    assert _methods_for(router, "/notes/{note_id}/pin") == {"POST"}
    assert _methods_for(router, "/notes/{note_id}/items/{index}/toggle") == {"POST"}
    assert _methods_for(router, "/memory") == {"GET", "POST"}
    assert _methods_for(router, "/memory/{memory_id}") == {"DELETE"}


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


# --- write gates: scope + resolvable owner ---------------------------------

def test_require_companion_scope_blocks_chat_only_token():
    from fastapi import HTTPException
    import pytest
    # A chat-only bearer token may not mutate companion data.
    with pytest.raises(HTTPException) as exc:
        require_companion_scope(_req(api_token=True, owner="alice", scopes=["chat"]))
    assert exc.value.status_code == 403
    # A companion-scoped token and a cookie session pass.
    require_companion_scope(_req(api_token=True, owner="alice", scopes=["companion"]))
    require_companion_scope(_req(api_token=False, current_user="alice"))


def test_writer_owner_refuses_ownerless_bearer_token():
    from fastapi import HTTPException
    import pytest
    # A bearer token with no resolvable owner must NOT fall through to mutating
    # shared null-owner rows — refuse it.
    with pytest.raises(HTTPException) as exc:
        writer_owner(_req(api_token=True, owner=None, scopes=["companion"]))
    assert exc.value.status_code == 401
    # A real owner resolves; a cookie/single-user (None) is allowed.
    assert writer_owner(_req(api_token=True, owner="alice", scopes=["companion"])) == "alice"
    assert writer_owner(_req(api_token=False, current_user=None)) is None


# --- delete is strictly owner-scoped (404, never confirm existence) --------

class _WriteQuery:
    def __init__(self, rows):
        self._rows = rows
        self._pred = None

    def filter(self, *a, **k):
        # The handlers filter by id==X; we only need to resolve .first(), so
        # match on the row whose id appears in the filter's repr is overkill —
        # tests pass a single-row DB and assert via owner, so just return self.
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _WriteDB:
    def __init__(self, rows):
        self._rows = rows
        self.deleted = []
        self.committed = False

    def query(self, *a, **k):
        return _WriteQuery(self._rows)

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _delete_note_handler():
    router = setup_companion_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/notes/{note_id}") and "DELETE" in (r.methods or set()):
            return r.endpoint
    raise AssertionError("DELETE /notes/{note_id} not found")


def test_delete_note_rejects_cross_owner_with_404(monkeypatch):
    from fastapi import HTTPException
    import pytest
    # The row belongs to bob; alice's token must get a 404 and NOT delete it.
    bobs_note = SimpleNamespace(id="n1", owner="bob")
    db = _WriteDB([bobs_note])
    _install_write_db(db, monkeypatch)
    handler = _delete_note_handler()
    req = _req(api_token=True, owner="alice", scopes=["companion"])
    with pytest.raises(HTTPException) as exc:
        handler(req, "n1")
    assert exc.value.status_code == 404
    assert db.deleted == []  # never touched another owner's row


def test_delete_note_allows_owner(monkeypatch):
    alices_note = SimpleNamespace(id="n1", owner="alice")
    db = _WriteDB([alices_note])
    _install_write_db(db, monkeypatch)
    handler = _delete_note_handler()
    req = _req(api_token=True, owner="alice", scopes=["companion"])
    assert handler(req, "n1") == {"ok": True}
    assert db.deleted == [alices_note] and db.committed


def _install_write_db(db, monkeypatch):
    class _DBStub(types.ModuleType):
        def __getattr__(self, name):
            return MagicMock()
    m = _DBStub("core.database")
    m.SessionLocal = lambda: db
    monkeypatch.setitem(sys.modules, "core.database", m)


# --- memory WRITES land in the store the READS come from --------------------
# Regression guard for a real split-brain: the reads were moved onto the live
# MemoryManager (memory.json) while the writes still went to the ORM `Memory`
# table, so a memory created on the phone was written somewhere nothing reads —
# invisible in both the mobile list and the web UI, and undeletable. These
# exercise the REAL MemoryManager over a tmp dir, so the two paths must agree.

def _memory_route(mm, path_suffix, method):
    router = setup_companion_routes(memory_manager=mm)
    for r in router.routes:
        if getattr(r, "path", "").endswith(path_suffix) and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"{method} {path_suffix} route not found")


def _real_manager(tmp_path):
    from src.memory import MemoryManager
    return MemoryManager(str(tmp_path))


def test_created_memory_is_visible_to_the_reader(tmp_path):
    mm = _real_manager(tmp_path)
    req = _req(api_token=True, owner="alice", scopes=_paired_scopes())

    created = _memory_route(mm, "/memory", "POST")(req, text="ship it", category="goal")
    assert created["text"] == "ship it" and created["category"] == "goal"

    # the SAME manager the app reads through must now return it
    listed = _memory_route(mm, "/memory", "GET")(req)["items"]
    assert [(m["id"], m["text"]) for m in listed] == [(created["id"], "ship it")]


def test_created_memory_is_owner_scoped_and_deletable(tmp_path):
    mm = _real_manager(tmp_path)
    alice = _req(api_token=True, owner="alice", scopes=_paired_scopes())
    bob = _req(api_token=True, owner="bob", scopes=_paired_scopes())

    created = _memory_route(mm, "/memory", "POST")(alice, text="alice only", category="fact")

    # bob neither sees it nor can delete it (404, not 403 — no existence probe)
    assert _memory_route(mm, "/memory", "GET")(bob)["items"] == []
    with pytest.raises(HTTPException) as exc:
        _memory_route(mm, "/memory/{memory_id}", "DELETE")(bob, created["id"])
    assert exc.value.status_code == 404
    assert len(_memory_route(mm, "/memory", "GET")(alice)["items"]) == 1

    # the owner can, and the read reflects it
    assert _memory_route(mm, "/memory/{memory_id}", "DELETE")(alice, created["id"]) == {"ok": True}
    assert _memory_route(mm, "/memory", "GET")(alice)["items"] == []


def test_unreadable_store_refuses_to_rewrite(tmp_path):
    # A transient read failure must not be mistaken for an empty store and
    # persisted over every existing memory (issue #5673) — 503, no write.
    from src.memory import MemoryStoreUnreadable

    mm = _real_manager(tmp_path)
    req = _req(api_token=True, owner="alice", scopes=_paired_scopes())
    _memory_route(mm, "/memory", "POST")(req, text="precious", category="fact")

    def _boom():
        raise MemoryStoreUnreadable("corrupt")

    mm.load_all_for_update = _boom
    with pytest.raises(HTTPException) as exc:
        _memory_route(mm, "/memory", "POST")(req, text="new", category="fact")
    assert exc.value.status_code == 503

    mm2 = _real_manager(tmp_path)
    assert [m["text"] for m in mm2.load(owner="alice")] == ["precious"]
