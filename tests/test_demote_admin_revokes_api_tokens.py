"""Demoting an admin purges their API tokens (issue #6831).

Token management is admin-only and scoped to the token's owner, so once the
owner is demoted nobody can list or revoke their tokens any more.
"""

import contextlib
import sys
import types

import pytest

from tests.test_delete_user_revokes_api_tokens import (
    _auth_module,
    _FakeApiToken,
    _FakeSession,
)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    auth_mod = _auth_module()
    monkeypatch.setattr(auth_mod, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(
        auth_mod, "_verify_password", lambda password, hashed: hashed == f"hash:{password}"
    )
    mgr = auth_mod.AuthManager(str(tmp_path / "auth.json"))
    assert mgr.create_user("admin", "secret-admin-pw", is_admin=True)
    assert mgr.create_user("adm2", "secret-adm2-pw", is_admin=True)
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


def test_demote_revokes_api_tokens(manager, db_calls):
    result = manager.set_admin("adm2", False, "admin")

    assert result.value == "ok"
    assert manager.is_admin("adm2") is False
    assert [("owner ==", "adm2")] in db_calls


def test_promote_leaves_api_tokens_alone(manager, db_calls):
    manager.set_admin("bob", True, "admin")

    assert db_calls == []


def test_refused_demotion_leaves_api_tokens_alone(manager, db_calls):
    assert manager.set_admin("adm2", False, "bob").value == "not_authorized"
    assert db_calls == []


def test_demote_fails_closed_when_token_purge_fails(manager, monkeypatch):
    @contextlib.contextmanager
    def _failing_db_session():
        raise RuntimeError("database unavailable")
        yield

    db_stub = types.ModuleType("core.database")
    db_stub.get_db_session = _failing_db_session
    db_stub.ApiToken = _FakeApiToken
    monkeypatch.setitem(sys.modules, "core.database", db_stub)

    with pytest.raises(RuntimeError):
        manager.set_admin("adm2", False, "admin")

    assert manager.is_admin("adm2") is True
