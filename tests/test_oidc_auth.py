"""Tests for AuthManager OIDC methods — user creation, lookup, and password rejection."""

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_auth_module():
    """Import the real core.auth module."""
    import importlib, sys
    if "core.auth" in sys.modules:
        return sys.modules["core.auth"]
    import core.auth
    return core.auth


def _make_manager(tmp_path: Path):
    """Create an AuthManager pointed at a temp auth.json."""
    auth = _real_auth_module()
    mgr = auth.AuthManager(str(tmp_path / "auth.json"))
    return mgr


# ---------------------------------------------------------------------------
# User creation
# ---------------------------------------------------------------------------

def test_create_user_oidc_basic(tmp_path):
    mgr = _make_manager(tmp_path)
    username = mgr.create_user_oidc(
        "alice", sub="abc123", issuer="https://idp.example.com", email="alice@example.com",
    )
    assert username == "alice"
    assert mgr.is_oidc_user("alice")
    assert "alice" in mgr.users
    assert mgr.users["alice"]["password_hash"] is None
    assert mgr.users["alice"]["oidc_sub"] == "abc123"
    assert mgr.users["alice"]["oidc_issuer"] == "https://idp.example.com"
    assert mgr.users["alice"]["oidc_email"] == "alice@example.com"


def test_create_user_oidc_lowercases_username(tmp_path):
    mgr = _make_manager(tmp_path)
    username = mgr.create_user_oidc(
        "Alice", sub="abc123", issuer="https://idp.example.com", email="alice@example.com",
    )
    assert username == "alice"


def test_create_user_oidc_rejects_reserved(tmp_path):
    mgr = _make_manager(tmp_path)
    for reserved in ("internal-tool", "api", "demo", "system"):
        username = mgr.create_user_oidc(
            reserved, sub="sub", issuer="https://idp.example.com",
        )
        assert username is None, f"Should reject reserved username {reserved!r}"


def test_create_user_oidc_empty_username(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.create_user_oidc("  ", sub="sub", issuer="https://idp.example.com") is None
    assert mgr.create_user_oidc("", sub="sub", issuer="https://idp.example.com") is None


def test_create_user_oidc_idempotent(tmp_path):
    """Calling create_user_oidc with the same (sub, issuer) returns the
    existing username, even if the suggested raw username differs."""
    mgr = _make_manager(tmp_path)
    first = mgr.create_user_oidc(
        "alice", sub="abc123", issuer="https://idp.example.com", email="alice@example.com",
    )
    second = mgr.create_user_oidc(
        "alice_renamed", sub="abc123", issuer="https://idp.example.com",
    )
    assert first == "alice"
    assert second == "alice"  # same identity, no new user created


def test_create_user_oidc_username_collision_with_password_user(tmp_path):
    """When the desired username is taken by a local password user, append
    a numeric suffix."""
    mgr = _make_manager(tmp_path)

    # Create a password user first
    ok = mgr.create_user("alice", "hunter2", is_admin=False)
    assert ok

    # Now try to create an OIDC user with the same username
    oidc_user = mgr.create_user_oidc(
        "alice", sub="oidc_sub", issuer="https://idp.example.com",
    )
    assert oidc_user is not None
    assert oidc_user != "alice"  # should get a different name
    assert oidc_user.startswith("alice")
    assert mgr.is_oidc_user(oidc_user)
    assert not mgr.is_oidc_user("alice")  # the password user is unaffected


def test_create_user_oidc_username_collision_with_other_oidc_user(tmp_path):
    """Two OIDC users with different identities but the same preferred
    username should get distinct accounts."""
    mgr = _make_manager(tmp_path)

    alice1 = mgr.create_user_oidc(
        "alice", sub="sub1", issuer="https://idp1.example.com",
    )
    alice2 = mgr.create_user_oidc(
        "alice", sub="sub2", issuer="https://idp2.example.com",
    )
    assert alice1 == "alice"
    assert alice2 is not None
    assert alice2 != "alice"
    assert alice2.startswith("alice")
    assert mgr.is_oidc_user(alice1)
    assert mgr.is_oidc_user(alice2)


def test_create_user_oidc_multiple_collisions(tmp_path):
    """A large number of collisions still resolves (suffix increment on each)."""
    mgr = _make_manager(tmp_path)

    # Create 5 users named "bob" through different identities
    usernames = set()
    for i in range(5):
        u = mgr.create_user_oidc(
            "bob", sub=f"sub_{i}", issuer="https://idp.example.com",
        )
        assert u is not None
        usernames.add(u)
    assert len(usernames) == 5
    assert "bob" in usernames
    assert "bob2" in usernames or "bob3" in usernames


# ---------------------------------------------------------------------------
# get_user_by_oidc
# ---------------------------------------------------------------------------

def test_get_user_by_oidc_found(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc(
        "alice", sub="abc123", issuer="https://idp.example.com",
    )
    assert mgr.get_user_by_oidc("abc123", "https://idp.example.com") == "alice"


def test_get_user_by_oidc_wrong_sub(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    assert mgr.get_user_by_oidc("wrong_sub", "https://idp.example.com") is None


def test_get_user_by_oidc_wrong_issuer(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    assert mgr.get_user_by_oidc("abc123", "https://other-idp.example.com") is None


def test_get_user_by_oidc_no_users(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.get_user_by_oidc("any", "https://any.example.com") is None


# ---------------------------------------------------------------------------
# is_oidc_user
# ---------------------------------------------------------------------------

def test_is_oidc_user_true(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    assert mgr.is_oidc_user("alice")


def test_is_oidc_user_false_for_password_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("bob", "hunter2")
    assert not mgr.is_oidc_user("bob")


def test_is_oidc_user_false_for_nonexistent(tmp_path):
    mgr = _make_manager(tmp_path)
    assert not mgr.is_oidc_user("ghost")


# ---------------------------------------------------------------------------
# Password rejection for OIDC users
# ---------------------------------------------------------------------------

def test_verify_password_rejects_oidc_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    assert not mgr.verify_password("alice", "any_password")


def test_create_session_rejects_oidc_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    token = mgr.create_session("alice", "any_password")
    assert token is None


def test_change_password_rejects_oidc_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    ok = mgr.change_password("alice", "any_password", "new_password")
    assert not ok


def test_oidc_user_session_via_create_session_trusted(tmp_path):
    """An OIDC user can still get a session via the trusted path (used
    after successful OIDC flow)."""
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    token = mgr.create_session_trusted("alice")
    assert token is not None
    assert mgr.validate_token(token)
    assert mgr.get_username_for_token(token) == "alice"


# ---------------------------------------------------------------------------
# list_users includes OIDC info
# ---------------------------------------------------------------------------

def test_list_users_includes_oidc_info(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("bob", "hunter2")
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com",
                          email="alice@example.com")

    users = mgr.list_users()
    alice_entry = next((u for u in users if u["username"] == "alice"), None)
    bob_entry = next((u for u in users if u["username"] == "bob"), None)

    assert alice_entry is not None
    assert alice_entry.get("oidc") is True
    assert alice_entry.get("oidc_issuer") == "https://idp.example.com"
    assert alice_entry.get("oidc_email") == "alice@example.com"

    assert bob_entry is not None
    assert bob_entry.get("oidc") is None  # password users don't have oidc flag


# ---------------------------------------------------------------------------
# set_oidc_user_admin
# ---------------------------------------------------------------------------

def test_set_oidc_user_admin_promotes(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc", issuer="https://idp.example.com",
                          is_admin=False)
    assert not mgr.is_admin("alice")
    assert mgr.set_oidc_user_admin("alice", True)
    assert mgr.is_admin("alice")
    # Privileges should be upgraded to ADMIN_PRIVILEGES
    privs = mgr.get_privileges("alice")
    assert privs["can_use_bash"] is True


def test_set_oidc_user_admin_demotes(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc", issuer="https://idp.example.com",
                          is_admin=True)
    assert mgr.is_admin("alice")
    assert mgr.set_oidc_user_admin("alice", False)
    assert not mgr.is_admin("alice")
    # Privileges should be downgraded to DEFAULT_PRIVILEGES
    privs = mgr.get_privileges("alice")
    assert privs["can_use_bash"] is False


def test_set_oidc_user_admin_noop_when_unchanged(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc", issuer="https://idp.example.com",
                          is_admin=False)
    assert mgr.set_oidc_user_admin("alice", False)  # still returns True
    assert not mgr.is_admin("alice")


def test_set_oidc_user_admin_rejects_non_oidc_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("bob", "hunter2", is_admin=False)
    # Can't promote a password user via this method
    assert not mgr.set_oidc_user_admin("bob", True)
    assert not mgr.is_admin("bob")


def test_set_oidc_user_admin_rejects_nonexistent(tmp_path):
    mgr = _make_manager(tmp_path)
    assert not mgr.set_oidc_user_admin("ghost", True)
