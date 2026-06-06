import types

import pytest

from src import auth_helpers
from src.auth_helpers import require_privilege


class _Mgr:
    def __init__(self, privs):
        self._privs = privs

    def get_privileges(self, user):
        return self._privs


def _request(mgr):
    state = types.SimpleNamespace(auth_manager=mgr)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


def test_require_privilege_tolerates_non_dict_privileges(monkeypatch):
    # A corrupt auth.json can make get_privileges return a non-dict (e.g. a
    # list). The privs.get(...) call sits outside the try, so the old code
    # raised AttributeError and turned a privilege check into a 500. It should
    # fall back to the documented fail-open behaviour.
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr(["do_x"]))
    assert require_privilege(req, "do_x") == "bob"


def test_require_privilege_still_blocks_disallowed(monkeypatch):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr({"do_x": False}))
    with pytest.raises(Exception):
        require_privilege(req, "do_x")
