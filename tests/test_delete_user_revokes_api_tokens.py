"""Deleting a user must also revoke their API bearer tokens.

Regression test: delete_user purged cookie sessions but left ApiToken
rows behind, so a deleted user could keep authenticating with an
"ody_..." bearer token forever.
"""

import contextlib
import importlib
import sys
import types
from pathlib import Path

import pytest


def _real_core_package():
    root = Path(__file__).resolve().parent.parent
    core_path = str(root / "core")
    core = sys.modules.get("core")
    if core is None:
        core = types.ModuleType("core")
        sys.modules["core"] = core
    core.__path__ = [core_path]
    if hasattr(core, "auth"):
        delattr(core, "auth")
    sys.modules.pop("core.auth", None)
    return core


def _auth_module():
    _real_core_package()
    return importlib.import_module("core.auth")


class _OwnerColumn:
    """Mimics a SQLAlchemy column: ApiToken.owner == x yields a marker."""

    def __eq__(self, other):
        return ("owner ==", other)

    def __hash__(self):
        return id(self)


class _FakeApiToken:
    owner = _OwnerColumn()


class _FakeQuery:
    def __init__(self, recorder):
        self._recorder = recorder
        self._conds = []

    def filter(self, *conds):
        self._conds.extend(conds)
        return self

    def delete(self, *args, **kwargs):
        self._recorder.append(list(self._conds))
        return len(self._conds)


class _FakeSession:
    def __init__(self, recorder):
        self._recorder = recorder

    def query(self, model):
        assert model is _FakeApiToken
        return _FakeQuery(self._recorder)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    auth_mod = _auth_module()
    monkeypatch.setattr(auth_mod, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(
        auth_mod, "_verify_password", lambda password, hashed: hashed == f"hash:{password}"
    )
    mgr = auth_mod.AuthManager(str(tmp_path / "auth.json"))
    assert mgr.create_user("admin", "secret-admin-pw", is_admin=True)
    assert mgr.create_user("bob", "secret-bob-pw", is_admin=False)
    return mgr


@pytest.fixture
def db_calls(monkeypatch):
    calls = []

    @contextlib.contextmanager
    def _fake_db_session():
        yield _FakeSession(calls)

    db_stub = types.ModuleType("core.database")
    db_stub.get_db_session = _fake_db_session
    db_stub.ApiToken = _FakeApiToken
    monkeypatch.setitem(sys.modules, "core.database", db_stub)
    return calls


def test_delete_user_revokes_api_tokens(manager, db_calls):
    assert manager.delete_user("bob", "admin") is True
    assert "bob" not in manager.users
    assert db_calls, "delete_user never purged ApiToken rows for the deleted user"
    assert [("owner ==", "bob")] in db_calls


def test_refused_delete_leaves_tokens_alone(manager, db_calls):
    assert manager.delete_user("admin", "bob") is False
    assert "admin" in manager.users
    assert db_calls == []


def test_unknown_user_leaves_tokens_alone(manager, db_calls):
    assert manager.delete_user("ghost", "admin") is False
    assert db_calls == []
