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

# Import the *real* core.session_manager + routes.session_routes under conftest's
# MagicMock sqlalchemy stub. The real core.database defines declarative classes
# that blow up under that stub, so temporarily swap in MagicMock module objects
# (auto-creating attributes satisfy any `from core.database import X`). Crucially
# we RESTORE both sys.modules AND the parent `routes` package attribute after
# import, so these stubs never leak into sibling modules — the local SM/SR
# bindings keep their captured stub modules for this file's own assertions.
_ABSENT = object()


def _save_module_and_parent_attr(dotted_name):
    """Capture a module's sys.modules entry *and* its parent-package attribute.

    Importing ``routes.session_routes`` also sets ``session_routes`` on the
    parent ``routes`` package object, and ``import routes.session_routes as X``
    resolves ``X`` through that parent attribute — so restoring sys.modules
    alone leaves the stale stub-bound module reachable. Returns a (module, attr)
    pair to hand back to _restore_module_and_parent_attr.
    """
    saved_module = sys.modules.get(dotted_name, _ABSENT)
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    saved_attr = getattr(pkg, attr, _ABSENT) if pkg is not None else _ABSENT
    return saved_module, saved_attr


def _restore_module_and_parent_attr(dotted_name, saved_module, saved_attr):
    """Restore (or remove) both the sys.modules entry and the parent attribute.

    Passing _ABSENT for both clears the cache, which is how we drop any stale
    entry before the stubbed import.
    """
    if saved_module is _ABSENT:
        sys.modules.pop(dotted_name, None)
    else:
        sys.modules[dotted_name] = saved_module
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        return
    if saved_attr is _ABSENT:
        if hasattr(pkg, attr):
            delattr(pkg, attr)
    else:
        setattr(pkg, attr, saved_attr)


_TEMP_STUBS = ("core.database", "core.models")
_saved = {name: sys.modules.get(name, _ABSENT) for name in _TEMP_STUBS}
_saved["core.session_manager"] = sys.modules.get("core.session_manager", _ABSENT)
_sr_saved = _save_module_and_parent_attr("routes.session_routes")
try:
    for _name in _TEMP_STUBS:
        sys.modules[_name] = MagicMock(name=_name)
    if isinstance(sys.modules.get("core.session_manager"), MagicMock):
        del sys.modules["core.session_manager"]
    # Clear the sys.modules entry AND the parent `routes` attribute so the
    # stubbed import below produces a fresh module with no stale binding behind it.
    _restore_module_and_parent_attr("routes.session_routes", _ABSENT, _ABSENT)
    SM = importlib.import_module("core.session_manager")
    import routes.session_routes as SR  # noqa: E402
finally:
    for _name, _val in _saved.items():
        if _val is _ABSENT:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _val
    _restore_module_and_parent_attr("routes.session_routes", *_sr_saved)

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
    assert exc.value.status_code == 403


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
