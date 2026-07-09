import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from core.auth import AuthManager
from core.ldap_auth import LdapLoginResult, group_aliases, group_set_matches
from routes.auth_routes import LoginRequest, setup_auth_routes


def _login_endpoint(auth_manager):
    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/auth/login":
            return route.endpoint
    raise AssertionError("login route not found")


class _Response:
    def __init__(self):
        self.cookie_kwargs = None

    def set_cookie(self, **kwargs):
        self.cookie_kwargs = kwargs


@pytest.fixture
def fast_hash(monkeypatch):
    import core.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(auth_mod, "_verify_password", lambda password, hashed: hashed == f"hash:{password}")


def test_group_aliases_match_freeipa_short_names_and_dns():
    groups = group_aliases("cn=odysseus-admins,cn=groups,cn=accounts,dc=example,dc=test")

    assert "odysseus-admins" in groups
    assert "cn=odysseus-admins,cn=groups,cn=accounts,dc=example,dc=test" in groups
    assert group_set_matches(groups, ["ODYSSEUS-ADMINS"])
    assert group_set_matches(groups, ["cn=odysseus-admins,cn=groups,cn=accounts,dc=example,dc=test"])


def test_ldap_sync_creates_shadow_user_without_password_hash(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda: True)
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password: LdapLoginResult(
            username="alice",
            user_dn="uid=alice,cn=users,dc=example,dc=test",
            display_name="Alice Example",
            email="alice@example.test",
            groups=("odysseus-users",),
            is_admin=False,
        ),
    )

    assert mgr.authenticate_ldap("Alice", "correct-password") == "alice"
    assert mgr.users["alice"]["auth_source"] == "ldap"
    assert mgr.users["alice"]["ldap_dn"] == "uid=alice,cn=users,dc=example,dc=test"
    assert "password_hash" not in mgr.users["alice"]
    assert mgr.verify_password("alice", "correct-password") is False


def test_local_account_wins_over_matching_ldap_username(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    mgr.create_user("alice", "local-password")
    ldap_called = False

    def fake_ldap(username, password):
        nonlocal ldap_called
        ldap_called = True
        return LdapLoginResult(username="alice", user_dn="uid=alice,dc=example")

    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda: True)
    monkeypatch.setattr("core.ldap_auth.authenticate_ldap", fake_ldap)

    assert mgr.can_attempt_ldap("alice") is False
    assert mgr.authenticate_ldap("alice", "directory-password") is None
    assert ldap_called is False
    assert mgr.verify_password("alice", "local-password") is True


def test_ldap_admin_group_sync_marks_shadow_user_admin(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda: True)
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password: LdapLoginResult(
            username="carol",
            user_dn="uid=carol,dc=example",
            groups=("odysseus-admins",),
            is_admin=True,
        ),
    )

    assert mgr.authenticate_ldap("carol", "directory-password") == "carol"
    assert mgr.is_admin("carol") is True


def test_login_route_falls_back_to_ldap_after_local_password_fails(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    endpoint = _login_endpoint(mgr)
    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda: True)
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password: LdapLoginResult(username="dana", user_dn="uid=dana,dc=example"),
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    response = _Response()

    result = asyncio.run(
        endpoint(
            body=LoginRequest(username="Dana", password="directory-password"),
            request=request,
            response=response,
        )
    )

    assert result == {"ok": True, "username": "dana"}
    assert response.cookie_kwargs["key"] == "odysseus_session"


def test_login_route_does_not_try_ldap_for_existing_local_user(monkeypatch):
    auth = MagicMock()
    auth.verify_password.return_value = False
    auth.can_attempt_ldap.return_value = False
    endpoint = _login_endpoint(auth)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint(
                body=LoginRequest(username="alice", password="wrong-password"),
                request=request,
                response=_Response(),
            )
        )

    assert exc.value.status_code == 401
    auth.authenticate_ldap.assert_not_called()
