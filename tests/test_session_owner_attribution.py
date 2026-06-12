"""Tests for token-owner session attribution (effective_user + session routes).

Proves the two properties the review asked for:
  - cookie/browser users are completely unchanged (no-op swap)
  - a bearer token for owner A can never read/verify owner B's session, and a
    bearer token with no owner does not escalate.

Follows the direct-helper + mocked-DB style of tests/test_null_owner_gates.py.
"""

import os
import sys
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.helpers.import_state import clear_module, preserve_import_state

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub heavy ORM modules so routes.session_routes can be imported under
# conftest's MagicMock sqlalchemy shim. preserve_import_state restores both the
# stubs and the cached route module — including the parent `routes`/`core`
# package attributes — on exit, preventing poisoning of later tests via
# `import routes.session_routes`.


def _set_module_and_parent_attr(dotted_name, module):
    """Install a module at both sys.modules *and* the parent-package attribute.

    Setting only sys.modules[...] leaves the parent `core` package attribute
    pointing at the previous (real) module, so a later import resolving through
    the parent would bypass the stub — and, symmetrically, a stub left on the
    parent attribute would poison later tests. Controlling both keeps the two
    views consistent so preserve_import_state can fully undo them.
    """
    sys.modules[dotted_name] = module
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    if pkg is not None:
        setattr(pkg, attr, module)


# Modules whose import-time effects leak through both sys.modules and the parent
# `core`/`routes` package attributes. core.database/core.models are stubbed so
# routes.session_routes imports under conftest's MagicMock sqlalchemy shim;
# core.session_manager and routes.session_routes are (re)imported fresh.
# preserve_import_state captures each at both levels and restores them on exit so
# this file cannot poison later tests via `import core.<...>` /
# `import routes.session_routes`.
_TEMP_STUBS = ("core.database", "core.models")
_MANAGED = _TEMP_STUBS + ("core.session_manager", "routes.session_routes")
with preserve_import_state(*_MANAGED):
    for _name in _TEMP_STUBS:
        _set_module_and_parent_attr(_name, MagicMock(name=_name))
    # Clear sys.modules AND the parent package attribute for the modules we
    # re-import so the stubbed import below yields fresh modules with no stale
    # binding reachable behind them.
    clear_module("core.session_manager")
    clear_module("routes.session_routes")
    importlib.import_module("core.session_manager")
    import routes.session_routes as SR  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from src.auth_helpers import effective_user  # noqa: E402


def _req(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


# --- effective_user: who a request is attributed to ------------------------

def test_cookie_user_is_unchanged():
    # The whole point: browser/cookie callers behave exactly as before.
    assert effective_user(_req(api_token=False, current_user="alice")) == "alice"


def test_bearer_token_attributes_to_its_owner():
    # A paired phone runs as the "api" pseudo-user but must act as the token owner.
    assert effective_user(_req(api_token=True, api_token_owner="alice", current_user="api")) == "alice"


def test_bearer_token_without_owner_does_not_escalate():
    # No owner on the token -> falls back to current_user ("api"), never another user.
    assert effective_user(_req(api_token=True, api_token_owner=None, current_user="api")) == "api"


# --- _verify_session_owner: bearer tokens cannot cross owners ---------------

def _session_local_returning(owner_value):
    """Mock SessionLocal whose query(...).filter(...).first() yields a row with
    the given owner (or None for 'no such session')."""
    db = MagicMock()
    row = None if owner_value is _MISSING else SimpleNamespace(owner=owner_value)
    db.query.return_value.filter.return_value.first.return_value = row
    return MagicMock(return_value=db)


_MISSING = object()


def test_bearer_owner_A_cannot_verify_owner_B_session(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("bob"))
    req = _req(api_token=True, api_token_owner="alice", current_user="api")
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(req, "sid-owned-by-bob")
    assert exc.value.status_code == 404


def test_owner_can_verify_their_own_session(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("alice"))
    req = _req(api_token=True, api_token_owner="alice", current_user="api")
    # Should not raise.
    SR._verify_session_owner(req, "sid-owned-by-alice")


def test_cookie_user_owns_their_session(monkeypatch):
    # Cookie path unchanged: alice (cookie) verifies alice's session.
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("alice"))
    req = _req(api_token=False, current_user="alice")
    SR._verify_session_owner(req, "sid")


def test_missing_session_is_404(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning(_MISSING))
    req = _req(api_token=False, current_user="alice")
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(req, "nope")
    assert exc.value.status_code == 404


def test_unauthenticated_caller_rejected(monkeypatch):
    req = _req(api_token=False, current_user=None)
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(req, "sid")
    assert exc.value.status_code == 401


def test_auth_disabled_allows_owner_stamped_session(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("admin"))
    req = _req(api_token=False, current_user=None)

    # Single-user/auth-disabled mode should verify existence but not compare owner.
    SR._verify_session_owner(req, "sid-owned-by-admin")
