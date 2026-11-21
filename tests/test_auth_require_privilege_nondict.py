"""require_privilege fails closed for a signed-in user.

A missing auth manager, an error reading privileges, a malformed privilege
record and a key the record does not carry all refuse the request. Single-
user mode (require_user returns "") is not enforced, and with
AUTH_ENABLED=false an unreadable privilege is still allowed, exactly as
before.
"""

import pathlib
import re
import types

import pytest
from fastapi import HTTPException

from core.auth import ADMIN_PRIVILEGES, DEFAULT_PRIVILEGES, AuthManager
from src import auth_helpers
from src.auth_helpers import require_privilege

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTE_KEYS = ("can_generate_images", "can_use_documents", "can_manage_memory", "can_share_notes", "can_use_research")


class _Mgr:
    def __init__(self, privs):
        self._privs = privs

    def get_privileges(self, user):
        return self._privs


class _Broken:
    def get_privileges(self, user):
        raise OSError("auth.json unreadable")


class _RealPrivileges:
    """AuthManager.get_privileges over an in-memory user map."""

    def __init__(self, users):
        self.users = users

    def get_privileges(self, user):
        return AuthManager.get_privileges(self, user)


def _request(mgr):
    state = types.SimpleNamespace(auth_manager=mgr)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    monkeypatch.setattr(auth_helpers, "_auth_disabled", lambda: False)


def _as(monkeypatch, user):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: user)


def _status(fn):
    with pytest.raises(HTTPException) as exc:
        fn()
    return exc.value.status_code


def test_non_dict_privileges_refuse_instead_of_erroring(monkeypatch):
    # A corrupt auth.json can make get_privileges return a list. That used to
    # be treated as "every privilege granted"; it now refuses with a 403
    # rather than a 500.
    _as(monkeypatch, "bob")
    assert _status(lambda: require_privilege(_request(_Mgr(["do_x"])), "do_x")) == 403


def test_explicit_false_blocks_and_explicit_true_allows(monkeypatch):
    _as(monkeypatch, "bob")
    assert _status(lambda: require_privilege(_request(_Mgr({"do_x": False})), "do_x")) == 403
    assert require_privilege(_request(_Mgr({"do_x": True})), "do_x") == "bob"


def test_unknown_key_is_refused(monkeypatch):
    _as(monkeypatch, "bob")
    assert _status(lambda: require_privilege(_request(_Mgr({"do_x": True})), "do_y")) == 403


def test_missing_manager_and_lookup_error_are_refused(monkeypatch):
    _as(monkeypatch, "bob")
    assert _status(lambda: require_privilege(_request(None), "can_use_research")) == 503
    assert _status(lambda: require_privilege(_request(_Broken()), "can_use_research")) == 503


@pytest.mark.parametrize("mgr", [None, _Broken(), _Mgr({}), _Mgr(["x"])])
def test_single_user_and_auth_disabled_modes_are_not_enforced(monkeypatch, mgr):
    # require_user returns "" for auth-disabled, loopback bypass and
    # unconfigured first-run; privileges are not enforced there.
    _as(monkeypatch, "")
    assert require_privilege(_request(mgr), "can_use_research") == ""


def test_auth_disabled_end_to_end(monkeypatch):
    monkeypatch.setattr(auth_helpers, "_auth_disabled", lambda: True)
    monkeypatch.setattr(auth_helpers, "get_current_user", lambda request: None)
    monkeypatch.setattr(auth_helpers, "_is_api_token_request", lambda request: False)
    assert require_privilege(_request(None), "can_generate_images") == ""


@pytest.mark.parametrize("mgr", [None, _Broken(), _Mgr({}), _Mgr(["x"])])
def test_auth_disabled_with_a_named_user_keeps_the_old_allowance(monkeypatch, mgr):
    # A deployment with AUTH_ENABLED=false can still carry a request user
    # (a middleware or a proxy sets one). It has opted out of accounts, so
    # an unreadable privilege stays allowed there; an explicit False still
    # blocks.
    monkeypatch.setattr(auth_helpers, "_auth_disabled", lambda: True)
    _as(monkeypatch, "alice")
    assert require_privilege(_request(mgr), "can_use_research") == "alice"
    assert _status(lambda: require_privilege(_request(_Mgr({"can_use_research": False})), "can_use_research")) == 403


@pytest.mark.parametrize("key", ROUTE_KEYS)
def test_every_route_privilege_works_for_a_user_and_an_admin(monkeypatch, key):
    mgr = _RealPrivileges({"bob": {"privileges": {}}, "root": {"is_admin": True},
                           "eve": {"privileges": {key: False}}})
    _as(monkeypatch, "bob")
    assert require_privilege(_request(mgr), key) == "bob"
    _as(monkeypatch, "root")
    assert require_privilege(_request(mgr), key) == "root"
    _as(monkeypatch, "eve")
    assert _status(lambda: require_privilege(_request(mgr), key)) == 403


def test_every_checked_key_is_a_default_privilege():
    """A key missing from DEFAULT_PRIVILEGES would now refuse every user, so
    every key passed to require_privilege anywhere must be listed there."""
    pattern = re.compile(r"require_privilege\(\s*[\w.]+\s*,\s*(?:privilege\s*=\s*)?(['\"])(\w+)\1")
    found = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).parts
        if rel[0] in {"tests", "venv", ".venv", "node_modules", "data"}:
            continue
        found |= {m.group(2) for m in pattern.finditer(path.read_text(encoding="utf-8", errors="ignore"))}
    assert set(ROUTE_KEYS) <= found, f"scanner missed callers: {set(ROUTE_KEYS) - found}"
    missing = found - set(DEFAULT_PRIVILEGES)
    assert not missing, f"require_privilege keys not in DEFAULT_PRIVILEGES: {sorted(missing)}"
    assert not (found - set(ADMIN_PRIVILEGES))


@pytest.mark.parametrize("key, verb", [
    ("can_share_notes", "share notes"),
    ("can_generate_images", "generate images"),
    ("can_use_research", "use research"),
])
def test_refusal_names_the_privilege_as_a_verb(monkeypatch, key, verb):
    # The key's "can_" prefix used to leak into the sentence: "not allowed to
    # can share notes".
    _as(monkeypatch, "bob")
    with pytest.raises(HTTPException) as exc:
        require_privilege(_request(_Mgr({key: False})), key)
    assert exc.value.detail == f"Your account is not allowed to {verb}."
