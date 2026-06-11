"""Promote/demote users to/from admin (issue #2958).

Covers AuthManager.set_admin (the core logic + last-admin lockout guard +
privilege stash/restore on a real role change + no-op preservation) and the
PUT /api/auth/users/{username}/admin route's status/envelope mapping.
"""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fastapi import HTTPException

from tests.helpers.import_state import clear_module


# ---------------------------------------------------------------------------
# Manager-level: real AuthManager on a temp auth.json (mirrors
# tests/test_rename_user_case_insensitive.py).
# ---------------------------------------------------------------------------

def _real_core_package():
    root = Path(__file__).resolve().parent.parent
    core_path = str(root / "core")
    core = sys.modules.get("core")
    if core is None:
        core = types.ModuleType("core")
        sys.modules["core"] = core
    core.__path__ = [core_path]
    clear_module("core.auth")
    return core


def _fresh_auth_manager(tmp_path):
    """Return (auth_module, AuthManager) with hashing stubbed for speed."""
    auth_mod = importlib.import_module("core.auth", package=_real_core_package())
    auth_mod._hash_password = lambda password: f"hash:{password}"
    auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    mgr = auth_mod.AuthManager(str(tmp_path / "auth.json"))
    return auth_mod, mgr


def test_promote_sets_admin_flag_and_admin_privileges(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    assert mgr.create_user("admin", "pw-123456", is_admin=True) is True
    assert mgr.create_user("bob", "pw-123456") is True

    result = mgr.set_admin("bob", True, "admin")

    assert result is auth_mod.SetAdminResult.OK
    assert mgr.is_admin("bob") is True
    assert mgr.users["bob"]["privileges"] == auth_mod.ADMIN_PRIVILEGES


def test_demote_with_two_admins_resets_to_default_privileges(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456", is_admin=True)

    result = mgr.set_admin("bob", False, "admin")

    assert result is auth_mod.SetAdminResult.OK
    assert mgr.is_admin("bob") is False
    assert mgr.users["bob"]["privileges"] == auth_mod.DEFAULT_PRIVILEGES


def test_demote_last_admin_is_blocked(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)

    result = mgr.set_admin("admin", False, "admin")

    assert result is auth_mod.SetAdminResult.LAST_ADMIN
    assert mgr.is_admin("admin") is True  # unchanged


def test_self_demote_allowed_when_another_admin_exists(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456", is_admin=True)

    result = mgr.set_admin("admin", False, "admin")  # admin demotes self

    assert result is auth_mod.SetAdminResult.OK
    assert mgr.is_admin("admin") is False
    assert mgr.is_admin("bob") is True


def test_cannot_demote_past_the_last_admin_sequentially(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456", is_admin=True)

    assert mgr.set_admin("bob", False, "admin") is auth_mod.SetAdminResult.OK
    # Now "admin" is the only admin left — demoting them must be refused.
    assert mgr.set_admin("admin", False, "admin") is auth_mod.SetAdminResult.LAST_ADMIN
    assert mgr.is_admin("admin") is True


def test_non_admin_requester_is_rejected(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456")
    mgr.create_user("carol", "pw-123456")

    result = mgr.set_admin("carol", True, "bob")  # bob is not an admin

    assert result is auth_mod.SetAdminResult.NOT_AUTHORIZED
    assert mgr.is_admin("carol") is False


def test_unknown_target_user_returns_not_found(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)

    result = mgr.set_admin("ghost", True, "admin")

    assert result is auth_mod.SetAdminResult.USER_NOT_FOUND


def test_noop_demote_of_regular_user_preserves_custom_privileges(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456")
    # Give bob a non-default privilege; DEFAULT_PRIVILEGES has can_use_bash=False.
    assert mgr.set_privileges("bob", {"can_use_bash": True}) is True

    result = mgr.set_admin("bob", False, "admin")  # already a regular user

    assert result is auth_mod.SetAdminResult.OK
    # Privileges must NOT have been reset to defaults by the no-op.
    assert mgr.users["bob"]["privileges"]["can_use_bash"] is True


def test_demote_restores_pre_admin_privilege_restrictions(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456")
    # Tighten bob below the defaults before promoting him.
    assert mgr.set_privileges("bob", {
        "can_use_agent": False,
        "can_generate_images": False,
        "max_messages_per_day": 50,
    }) is True
    restricted = mgr.get_privileges("bob")

    assert mgr.set_admin("bob", True, "admin") is auth_mod.SetAdminResult.OK
    assert mgr.set_admin("bob", False, "admin") is auth_mod.SetAdminResult.OK

    # Demotion must restore the pre-admin policy, not reset to defaults.
    assert mgr.get_privileges("bob") == restricted
    assert mgr.get_privileges("bob")["can_use_agent"] is False
    assert mgr.get_privileges("bob")["max_messages_per_day"] == 50


def test_promote_demote_round_trip_is_stable_and_cleans_up_stash(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456")
    assert mgr.set_privileges("bob", {"can_use_browser": False}) is True
    restricted = mgr.get_privileges("bob")

    for _ in range(2):  # two full promote/demote cycles
        assert mgr.set_admin("bob", True, "admin") is auth_mod.SetAdminResult.OK
        assert mgr.set_admin("bob", False, "admin") is auth_mod.SetAdminResult.OK

    assert mgr.get_privileges("bob") == restricted
    # The stash is promotion-time bookkeeping; it must not linger on the row.
    assert "privileges_before_admin" not in mgr.users["bob"]


def test_redundant_promote_does_not_clobber_stash(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456")
    assert mgr.set_privileges("bob", {"can_use_agent": False}) is True
    restricted = mgr.get_privileges("bob")

    assert mgr.set_admin("bob", True, "admin") is auth_mod.SetAdminResult.OK
    # A second promote is a no-op and must not re-stash ADMIN_PRIVILEGES.
    assert mgr.set_admin("bob", True, "admin") is auth_mod.SetAdminResult.OK
    assert mgr.set_admin("bob", False, "admin") is auth_mod.SetAdminResult.OK

    # Demotion must still restore the original pre-admin restrictions.
    assert mgr.get_privileges("bob") == restricted
    assert mgr.get_privileges("bob")["can_use_agent"] is False


def test_pre_admin_privileges_survive_manager_reload(tmp_path):
    auth_mod, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("admin", "pw-123456", is_admin=True)
    mgr.create_user("bob", "pw-123456")
    assert mgr.set_privileges("bob", {"can_use_research": False}) is True
    assert mgr.set_admin("bob", True, "admin") is auth_mod.SetAdminResult.OK

    # Fresh manager on the same auth.json — the stash must round-trip disk.
    mgr2 = auth_mod.AuthManager(str(tmp_path / "auth.json"))
    assert mgr2.set_admin("bob", False, "admin") is auth_mod.SetAdminResult.OK
    assert mgr2.get_privileges("bob")["can_use_research"] is False


# ---------------------------------------------------------------------------
# Route-level: PUT /api/auth/users/{username}/admin (mirrors
# tests/test_auth_regressions.py). SetAdminResult is read from the route
# module's own namespace so the route and the test share one enum object.
# ---------------------------------------------------------------------------

_ADMIN_ROUTE = "/api/auth/users/{username}/admin"


def _auth_route_endpoint(path, method):
    from routes.auth_routes import setup_auth_routes

    auth_manager = MagicMock()
    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return auth_manager, route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def _fake_auth_request(token="session-token"):
    from routes.auth_routes import SESSION_COOKIE

    req = SimpleNamespace()
    req.cookies = {SESSION_COOKIE: token}
    req.client = SimpleNamespace(host="127.0.0.1")
    return req


def _result_enum():
    import routes.auth_routes as ar

    return ar.SetAdminResult


def test_route_requires_admin():
    from routes.auth_routes import SetAdminRequest

    auth, target = _auth_route_endpoint(_ADMIN_ROUTE, "PUT")
    auth.get_username_for_token.return_value = "bob"
    auth.is_admin.return_value = False

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(username="carol", body=SetAdminRequest(is_admin=True),
                           request=_fake_auth_request()))

    assert exc.value.status_code == 403
    auth.set_admin.assert_not_called()


def test_route_last_admin_returns_400():
    from routes.auth_routes import SetAdminRequest

    R = _result_enum()
    auth, target = _auth_route_endpoint(_ADMIN_ROUTE, "PUT")
    auth.get_username_for_token.return_value = "admin"
    auth.is_admin.return_value = True
    auth.set_admin.return_value = R.LAST_ADMIN

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(username="admin", body=SetAdminRequest(is_admin=False),
                           request=_fake_auth_request()))

    assert exc.value.status_code == 400


def test_route_user_not_found_returns_404():
    from routes.auth_routes import SetAdminRequest

    R = _result_enum()
    auth, target = _auth_route_endpoint(_ADMIN_ROUTE, "PUT")
    auth.get_username_for_token.return_value = "admin"
    auth.is_admin.return_value = True
    auth.set_admin.return_value = R.USER_NOT_FOUND

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(username="ghost", body=SetAdminRequest(is_admin=True),
                           request=_fake_auth_request()))

    assert exc.value.status_code == 404


def test_route_success_returns_envelope():
    from routes.auth_routes import SetAdminRequest

    R = _result_enum()
    auth, target = _auth_route_endpoint(_ADMIN_ROUTE, "PUT")
    auth.get_username_for_token.return_value = "admin"
    auth.is_admin.return_value = True
    auth.set_admin.return_value = R.OK

    out = asyncio.run(target(username="bob", body=SetAdminRequest(is_admin=True),
                             request=_fake_auth_request()))

    assert out == {"ok": True, "is_admin": True, "self": False}


def test_route_self_flag_true_when_targeting_own_account():
    from routes.auth_routes import SetAdminRequest

    R = _result_enum()
    auth, target = _auth_route_endpoint(_ADMIN_ROUTE, "PUT")
    auth.get_username_for_token.return_value = "admin"
    auth.is_admin.return_value = True
    auth.set_admin.return_value = R.OK

    out = asyncio.run(target(username="Admin", body=SetAdminRequest(is_admin=False),
                             request=_fake_auth_request()))

    assert out == {"ok": True, "is_admin": False, "self": True}
