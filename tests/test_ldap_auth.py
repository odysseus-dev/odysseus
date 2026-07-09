import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from core.auth import AuthManager, SetAdminResult
from core.ldap_auth import LdapConfig, LdapLoginResult, group_aliases, group_set_matches
from routes.auth_routes import LoginRequest, SESSION_COOKIE, setup_auth_routes


def _login_endpoint(auth_manager):
    return _route_endpoint(auth_manager, "/api/auth/login", "POST")


def _route_endpoint(auth_manager, path, method):
    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _auth_request(token="session-token"):
    return SimpleNamespace(
        cookies={SESSION_COOKIE: token},
        client=SimpleNamespace(host="127.0.0.1"),
    )


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


def test_group_dn_setting_strings_are_not_split_on_dn_commas():
    config = LdapConfig.from_mapping(
        {
            "admin_groups": "cn=odysseus-admins,cn=groups,cn=accounts,dc=example,dc=test",
            "allowed_groups": "users;cn=allowed,cn=groups,dc=example,dc=test",
        },
        env_fallback=False,
    )

    assert config.admin_groups == ("cn=odysseus-admins,cn=groups,cn=accounts,dc=example,dc=test",)
    assert config.allowed_groups == ("users", "cn=allowed,cn=groups,dc=example,dc=test")


def test_ldap_sync_creates_shadow_user_without_password_hash(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda config=None: True)
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password, config=None: LdapLoginResult(
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

    def fake_ldap(username, password, config=None):
        nonlocal ldap_called
        ldap_called = True
        return LdapLoginResult(username="alice", user_dn="uid=alice,dc=example")

    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda config=None: True)
    monkeypatch.setattr("core.ldap_auth.authenticate_ldap", fake_ldap)

    assert mgr.can_attempt_ldap("alice") is False
    assert mgr.authenticate_ldap("alice", "directory-password") is None
    assert ldap_called is False
    assert mgr.verify_password("alice", "local-password") is True


def test_ldap_admin_group_sync_marks_shadow_user_admin(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda config=None: True)
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password, config=None: LdapLoginResult(
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
    monkeypatch.setattr("core.ldap_auth.ldap_enabled", lambda config=None: True)
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password, config=None: LdapLoginResult(username="dana", user_dn="uid=dana,dc=example"),
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


def test_ldap_settings_are_persisted_and_redacted(tmp_path, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))

    saved = mgr.set_ldap_settings(
        {
            "enabled": True,
            "server_uri": "ldap://ipa.example.test",
            "bind_dn": "uid=odysseus,cn=users,dc=example,dc=test",
            "bind_password": "super-secret",
            "user_base_dn": "cn=users,dc=example,dc=test",
            "allowed_groups": ["odysseus-users", "cn=admins,dc=example,dc=test"],
            "admin_groups": ["odysseus-admins"],
            "start_tls": "true",
        }
    )

    assert saved["enabled"] is True
    assert saved["bind_password"] == ""
    assert saved["bind_password_configured"] is True
    assert saved["allowed_groups"] == ["odysseus-users", "cn=admins,dc=example,dc=test"]
    assert saved["admin_groups"] == ["odysseus-admins"]
    assert saved["start_tls"] is True
    reloaded = AuthManager(str(tmp_path / "auth.json"))
    assert reloaded.ldap_config().bind_password == "super-secret"
    assert reloaded.ldap_settings()["bind_password"] == ""


def test_ldap_settings_blank_password_keeps_existing_secret(tmp_path, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    mgr.set_ldap_settings(
        {
            "enabled": True,
            "server_uri": "ldap://ipa.example.test",
            "bind_password": "first-secret",
            "user_base_dn": "cn=users,dc=example,dc=test",
        }
    )

    mgr.set_ldap_settings(
        {
            "enabled": True,
            "server_uri": "ldap://ipa2.example.test",
            "bind_password": "",
            "user_base_dn": "cn=users,dc=example,dc=test",
        }
    )

    assert mgr.ldap_config().server_uri == "ldap://ipa2.example.test"
    assert mgr.ldap_config().bind_password == "first-secret"


def test_ldap_test_login_returns_admin_diagnostics(tmp_path, monkeypatch, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    mgr.set_ldap_settings(
        {
            "enabled": True,
            "server_uri": "ldap://ipa.example.test",
            "user_base_dn": "cn=users,dc=example,dc=test",
            "allowed_groups": ["odysseus-users"],
            "admin_groups": ["odysseus-admins"],
        }
    )
    monkeypatch.setattr(
        "core.ldap_auth.authenticate_ldap",
        lambda username, password, config=None: LdapLoginResult(
            username="erin",
            user_dn="uid=erin,cn=users,dc=example,dc=test",
            groups=("odysseus-users", "odysseus-admins"),
            is_admin=True,
        ),
    )

    result = mgr.ldap_test_login("Erin", "directory-password")

    assert result["ok"] is True
    assert result["stage"] == "authenticated"
    assert result["username"] == "erin"
    assert result["is_admin"] is True
    assert result["in_allowed_group"] is True


def test_ldap_group_managed_admin_status_blocks_manual_changes(tmp_path, fast_hash):
    mgr = AuthManager(str(tmp_path / "auth.json"))
    mgr.create_user("admin", "local-password", is_admin=True)
    mgr._config.setdefault("users", {})["alice"] = {
        "auth_source": "ldap",
        "is_admin": True,
        "privileges": {},
        "created": 1,
    }
    mgr.set_ldap_settings(
        {
            "enabled": True,
            "server_uri": "ldap://ipa.example.test",
            "user_base_dn": "cn=users,dc=example,dc=test",
            "admin_groups": ["odysseus-admins"],
        }
    )

    assert mgr.set_admin("alice", False, "admin") is SetAdminResult.LDAP_MANAGED
    listed = {u["username"]: u for u in mgr.list_users()}
    assert listed["alice"]["admin_managed_by_ldap"] is True


def test_ldap_test_route_requires_admin_and_delegates():
    from routes.auth_routes import LdapTestLoginRequest

    auth = MagicMock()
    auth.get_username_for_token.return_value = "admin"
    auth.is_admin.return_value = True
    auth.ldap_test_login.return_value = {"ok": True, "stage": "authenticated"}
    endpoint = _route_endpoint(auth, "/api/auth/ldap-settings/test", "POST")

    result = asyncio.run(
        endpoint(
            body=LdapTestLoginRequest(username="alice", password="directory-password"),
            request=_auth_request(),
        )
    )

    assert result == {"ok": True, "stage": "authenticated"}
    auth.ldap_test_login.assert_called_once_with("alice", "directory-password", None)
