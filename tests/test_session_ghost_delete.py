"""Regression tests for issue #1044 — "ghost" sessions that appear in the list
but 404 on every operation and can never be deleted.

A ghost session lives only in the in-memory ``SessionManager`` (it was never
persisted, or its DB row was removed out-of-band). ``GET /api/sessions`` lists
sessions from the in-memory manager, so a ghost shows up; but ``_verify_session_owner``
only consulted the DB, so every per-session op 404'd, and ``SessionManager.delete_session``
only dropped the in-memory copy when a DB row existed — so the ghost was undeletable.

These tests pin both halves of the fix while proving the ownership/security model
is preserved (a ghost owned by another user still 404s; the DB row stays
authoritative when present).

Style mirrors tests/test_session_owner_attribution.py: stub the heavy ORM modules
so the real route + manager code can be imported under the MagicMock sqlalchemy
stub from conftest.
"""

import sys
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.helpers.import_state import clear_module, preserve_import_state

# Import the *real* core.session_manager + routes.session_routes under conftest's
# MagicMock sqlalchemy stub. The real core.database defines declarative classes
# that blow up under that stub, so temporarily swap in MagicMock module objects
# (auto-creating attributes satisfy any `from core.database import X`). Crucially
# preserve_import_state restores both sys.modules AND the parent `routes`/`core`
# package attributes after import, so these stubs never leak into sibling modules
# — the local SM/SR bindings keep their captured stub modules for this file's own
# assertions.
_TEMP_STUBS = ("core.database", "core.models")
with preserve_import_state(*_TEMP_STUBS, "core.session_manager", "routes.session_routes"):
    for _name in _TEMP_STUBS:
        sys.modules[_name] = MagicMock(name=_name)
    if isinstance(sys.modules.get("core.session_manager"), MagicMock):
        del sys.modules["core.session_manager"]
    # Drop the cached entry AND the parent `routes` attribute so the stubbed
    # import below yields a fresh module with no stale binding behind it.
    clear_module("routes.session_routes")
    SM = importlib.import_module("core.session_manager")
    import routes.session_routes as SR  # noqa: E402

from fastapi import HTTPException  # noqa: E402


_MISSING = object()


def _req(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


def _session_local_returning(owner_value):
    """Mock SessionLocal whose query(...).filter(...).first() yields a row with
    the given owner, or None when owner_value is _MISSING ('no DB row')."""
    db = MagicMock()
    row = None if owner_value is _MISSING else SimpleNamespace(owner=owner_value)
    db.query.return_value.filter.return_value.first.return_value = row
    return MagicMock(return_value=db)


def _manager_with(sessions):
    """A SessionManager instance with the given in-memory sessions and no __init__."""
    mgr = SM.SessionManager.__new__(SM.SessionManager)
    mgr.sessions = dict(sessions)
    return mgr


# --- route layer: _verify_session_owner ghost fallback ---------------------

def test_owned_ghost_is_allowed_when_manager_passed(monkeypatch):
    # No DB row, but the caller owns the in-memory ghost -> must NOT raise.
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning(_MISSING))
    sm = SimpleNamespace(sessions={"ghost": SimpleNamespace(owner="alice")})
    SR._verify_session_owner(_req(api_token=False, current_user="alice"), "ghost", sm)


def test_ghost_owned_by_another_user_still_404(monkeypatch):
    # Security: a ghost owned by bob must never be reachable by alice.
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning(_MISSING))
    sm = SimpleNamespace(sessions={"ghost": SimpleNamespace(owner="bob")})
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(_req(api_token=False, current_user="alice"), "ghost", sm)
    assert exc.value.status_code == 404


def test_no_manager_keeps_legacy_404(monkeypatch):
    # Backward compat: callers that don't pass a manager behave exactly as before.
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning(_MISSING))
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(_req(api_token=False, current_user="alice"), "ghost")
    assert exc.value.status_code == 404


def test_db_row_stays_authoritative(monkeypatch):
    # When a DB row exists it wins; the ghost map is not consulted.
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("alice"))
    sm = SimpleNamespace(sessions={"sid": SimpleNamespace(owner="bob")})
    SR._verify_session_owner(_req(api_token=False, current_user="alice"), "sid", sm)


def test_unauthenticated_still_403(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning(_MISSING))
    sm = SimpleNamespace(sessions={"ghost": SimpleNamespace(owner=None)})
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(_req(api_token=False, current_user=None), "ghost", sm)
    assert exc.value.status_code == 401


# --- manager layer: delete_session clears memory-only ghosts ---------------

def test_manager_deletes_memory_only_ghost(monkeypatch):
    # No DB row, but the session is in memory -> delete it and report success.
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(SM, "SessionLocal", MagicMock(return_value=fake_db))
    mgr = _manager_with({"ghost": SimpleNamespace(id="ghost", owner="alice")})
    assert mgr.delete_session("ghost") is True
    assert "ghost" not in mgr.sessions


def test_manager_delete_unknown_returns_false(monkeypatch):
    # Nothing in the DB and nothing in memory -> nothing deleted.
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(SM, "SessionLocal", MagicMock(return_value=fake_db))
    mgr = _manager_with({})
    assert mgr.delete_session("nope") is False
