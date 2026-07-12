"""Tests for AuthManager OIDC methods — user creation, lookup, and password rejection."""

import json
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
    # Disable auto-bootstrap so is_admin=False is respected
    import os
    os.environ["OIDC_FIRST_USER_IS_ADMIN"] = "false"
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


def test_first_user_bootstrap_suppressed_when_admin_groups_configured(
    tmp_path, monkeypatch,
):
    """When OIDC_ADMIN_GROUPS is set, the first OIDC user must be in a
    group to get admin — bootstrap does NOT override group-based policy."""
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")
    mgr = _make_manager(tmp_path)
    username = mgr.create_user_oidc(
        "alice", sub="abc", issuer="https://idp.example.com", is_admin=False,
    )
    assert username == "alice"
    assert not mgr.is_admin("alice"), (
        "First OIDC user should NOT be admin when OIDC_ADMIN_GROUPS is set "
        "and they are not in a group"
    )


# ---------------------------------------------------------------------------
# Route-level OIDC guards — 2FA and change-password
# ---------------------------------------------------------------------------

class TestOidcRouteGuards:
    """The auth routes reject local 2FA / password mutations for OIDC users.

    OIDC users authenticate through their identity provider; local password
    and TOTP controls are not applicable.  The frontend already hides these
    cards, but the backend must also enforce the policy so a direct API call
    cannot create a misleading or stuck 2FA state."""

    @pytest.fixture
    def setup_router(self, tmp_path):
        """Create an auth router backed by a temp AuthManager with one OIDC user."""
        from routes.auth_routes import setup_auth_routes
        mgr = _make_manager(tmp_path)
        mgr.create_user_oidc("alice", sub="abc", issuer="https://idp.example.com")
        # Issue a session so the user is "logged in"
        token = mgr.create_session_trusted("alice")
        router = setup_auth_routes(mgr)
        return router, mgr, token

    def _get(self, router, path):
        for route in router.routes:
            if getattr(route, "path", "") == path:
                return route.endpoint
        raise AssertionError(f"No route for {path}")

    def _fake_req(self, token):
        """Build a fake request with the session cookie set."""
        from types import SimpleNamespace
        req = SimpleNamespace()
        req.cookies = {"odysseus_session": token}
        req.client = SimpleNamespace()
        req.client.host = "127.0.0.1"
        return req

    def test_change_password_rejected_for_oidc_user(self, setup_router):
        router, mgr, token = setup_router
        ep = self._get(router, "/api/auth/change-password")
        from pydantic import BaseModel
        class PW(BaseModel):
            current_password: str = "x"
            new_password: str = "password123"
        import asyncio
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(PW(), self._fake_req(token)))
        assert exc.value.status_code == 400
        assert "OIDC" in exc.value.detail

    def test_2fa_setup_rejected_for_oidc_user(self, setup_router):
        router, mgr, token = setup_router
        ep = self._get(router, "/api/auth/2fa/setup")
        import asyncio
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(self._fake_req(token)))
        assert exc.value.status_code == 400
        assert "identity provider" in exc.value.detail.lower()

    def test_2fa_confirm_rejected_for_oidc_user(self, setup_router):
        router, mgr, token = setup_router
        ep = self._get(router, "/api/auth/2fa/confirm")
        from pydantic import BaseModel
        class TOTP(BaseModel):
            code: str = "123456"
        import asyncio
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(TOTP(), self._fake_req(token)))
        assert exc.value.status_code == 400
        assert "identity provider" in exc.value.detail.lower()

    def test_2fa_disable_rejected_for_oidc_user(self, setup_router):
        router, mgr, token = setup_router
        ep = self._get(router, "/api/auth/2fa/disable")
        from pydantic import BaseModel
        class DisableTOTP(BaseModel):
            password: str = "x"
        import asyncio
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(DisableTOTP(), self._fake_req(token)))
        assert exc.value.status_code == 400
        assert "identity provider" in exc.value.detail.lower()

    def test_password_user_still_can_use_2fa(self, setup_router):
        """Regression: local password users must still be able to manage 2FA."""
        router, mgr, token = setup_router
        # Add a local password user
        mgr.create_user("bob", "hunter2")
        bob_token = mgr.create_session_trusted("bob")
        ep = self._get(router, "/api/auth/2fa/setup")
        import asyncio
        # Should NOT raise — bob is a password user
        result = asyncio.run(ep(self._fake_req(bob_token)))
        assert "secret" in result
        assert "uri" in result


class TestFirstOidcAdminBootstrapConcurrency:
    """Regression: two concurrent first-OIDC-login callbacks must not
    both persist as admin.  The first-user bootstrap is serialized
    inside _config_lock."""

    def test_concurrent_first_oidc_users_only_one_admin(self, monkeypatch):
        """Simulate two fresh callbacks racing to create the first OIDC
        user.  The lock guarantees exactly one bootstrap admin, not two."""
        monkeypatch.setenv("OIDC_FIRST_USER_IS_ADMIN", "true")
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        from core.auth import AuthManager
        import threading
        import tempfile
        import os

        auth_path = os.path.join(tempfile.mkdtemp(), "auth.json")
        # Start with an empty auth store
        with open(auth_path, "w") as f:
            json.dump({}, f)

        mgr = AuthManager(auth_path)
        assert len(mgr.users) == 0

        results = []
        errors = []

        def create_user_a():
            try:
                u = mgr.create_user_oidc("alice", "sub-a", "https://idp.example.com")
                results.append(("alice", u, mgr.users.get(u, {}).get("is_admin", False)))
            except Exception as e:
                errors.append(e)

        def create_user_b():
            try:
                u = mgr.create_user_oidc("bob", "sub-b", "https://idp.example.com")
                results.append(("bob", u, mgr.users.get(u, {}).get("is_admin", False)))
            except Exception as e:
                errors.append(e)

        # Start both threads and wait for completion
        t1 = threading.Thread(target=create_user_a)
        t2 = threading.Thread(target=create_user_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(results) == 2

        # Exactly one user must be admin — the first one through the lock.
        admin_count = sum(1 for _, _, is_admin in results if is_admin)
        assert admin_count == 1, (
            f"Expected exactly 1 bootstrap admin, got {admin_count}. "
            f"Results: {results}"
        )

    def test_concurrent_same_identity_idempotent(self, monkeypatch):
        """Two concurrent create_user_oidc calls for the same OIDC identity
        must return the same username (idempotent inside the lock)."""
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        from core.auth import AuthManager
        import threading
        import tempfile
        import os

        auth_path = os.path.join(tempfile.mkdtemp(), "auth.json")
        with open(auth_path, "w") as f:
            json.dump({}, f)

        mgr = AuthManager(auth_path)

        results = []

        def create_same():
            u = mgr.create_user_oidc("charlie", "sub-c", "https://idp.example.com")
            results.append(u)

        t1 = threading.Thread(target=create_same)
        t2 = threading.Thread(target=create_same)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2
        # Both must return the same username — no duplicates
        assert results[0] == results[1]
        # Only one user entry must exist
        assert len(mgr.users) == 1


class TestInterprocessFirstAdminSerialisation:
    """Two independent AuthManager instances sharing the same auth.json
    path must serialise the first-admin decision across processes — the
    inter-process file lock (fcntl.flock) must prevent two workers from
    both creating an admin when the store is empty."""

    def test_two_managers_single_first_admin(self, tmp_path, monkeypatch):
        """Two managers with the same auth path: if one calls create_user_oidc
        first, the other's setup must see the store is already configured."""
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        from core.auth import AuthManager
        import threading

        auth_path = str(tmp_path / "auth.json")

        mgr_a = AuthManager(auth_path)
        mgr_b = AuthManager(auth_path)

        results = {}
        barrier = threading.Barrier(2, timeout=5)

        def oidc_first():
            barrier.wait()
            u = mgr_a.create_user_oidc("alice", "sub-a", "https://idp.example.com")
            results["oidc"] = u

        def local_setup():
            barrier.wait()
            ok = mgr_b.setup("admin", "password123")
            results["setup"] = ok

        t_oidc = threading.Thread(target=oidc_first)
        t_setup = threading.Thread(target=local_setup)
        t_oidc.start()
        t_setup.start()
        t_oidc.join()
        t_setup.join()

        # The inter-process lock serialises the critical sections.
        # The first operation through the lock sees an empty store and
        # creates an admin.  The second operation may still succeed at
        # creating a *non-admin* user (different username → no collision).
        # The key property: exactly one admin must exist.
        oidc_created = results.get("oidc") is not None
        setup_created = results.get("setup") is True
        assert oidc_created or setup_created, (
            f"At least one first-admin path must succeed; "
            f"oidc={oidc_created}, setup={setup_created}"
        )

        # Reload mgr_a and verify exactly one user is admin.
        mgr_a._load()
        admin_count = sum(1 for u in mgr_a.users.values() if u.get("is_admin"))
        assert admin_count == 1, (
            f"Expected exactly 1 admin after concurrent bootstrap; "
            f"found {admin_count}. Users: {list(mgr_a.users.keys())}"
        )

    def test_setup_sees_oidc_bootstrap(self, tmp_path, monkeypatch):
        """After create_user_oidc bootstraps the first admin, a subsequent
        setup() call on a different manager must see is_configured == True."""
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        from core.auth import AuthManager

        auth_path = str(tmp_path / "auth.json")

        mgr_a = AuthManager(auth_path)
        mgr_b = AuthManager(auth_path)

        # Manager A creates the first OIDC user (bootstrap admin)
        username = mgr_a.create_user_oidc("bob", "sub-b", "https://idp.example.com")
        assert username is not None
        assert len(mgr_a.users) == 1

        # Manager B: setup must now be denied — the store is configured
        ok = mgr_b.setup("admin", "password123")
        assert ok is False, "setup must not succeed when OIDC already bootstrapped"

    def test_set_oidc_user_admin_preserves_concurrent_user(self, tmp_path):
        """set_oidc_user_admin() must not overwrite users created by another
        manager.  Manager A creates 'alice' and 'bob', but Manager B has a
        stale in-memory snapshot.  When B calls set_oidc_user_admin for
        'alice', the inter-process lock + reload must preserve 'bob'."""
        from core.auth import AuthManager

        auth_path = str(tmp_path / "auth.json")

        # Manager A: create two OIDC users
        mgr_a = AuthManager(auth_path)
        alice = mgr_a.create_user_oidc("alice", "sub-a", "https://idp.example.com")
        assert alice == "alice"
        bob = mgr_a.create_user_oidc("bob", "sub-b", "https://idp.example.com")
        assert bob == "bob"
        # Make alice an admin so the no-op short-circuit doesn't trigger
        mgr_a._config["users"]["alice"]["is_admin"] = True
        mgr_a._save()

        # Manager B: loaded from disk but now we make its in-memory state
        # stale by directly removing 'bob' from its _config (simulating a
        # worker that loaded before Manager A created 'bob').
        mgr_b = AuthManager(auth_path)
        assert "bob" in mgr_b._config["users"]
        del mgr_b._config["users"]["bob"]

        # B calls set_oidc_user_admin for alice (demote).  The inter-process
        # lock must force a reload, re-discovering 'bob', so the save does
        # not clobber bob.
        result = mgr_b.set_oidc_user_admin("alice", False)
        assert result is True

        # Reload both managers — bob must still exist.
        mgr_a._load()
        mgr_b._load()
        assert "bob" in mgr_a._config["users"], "bob must survive stale set_oidc_user_admin"
        assert "bob" in mgr_b._config["users"], "bob must survive stale set_oidc_user_admin"
        assert not mgr_a._config["users"]["alice"].get("is_admin"), "alice must be demoted"

    def test_fcntl_import_guarded(self, monkeypatch):
        """On a platform without fcntl (simulated), AuthManager must still
        import and _interprocess_auth_lock must degrade to intra-process-only."""
        import sys
        import builtins

        # Simulate missing fcntl by hiding it from the import system
        real_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("No module named 'fcntl'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocking_import)

        # Force a fresh import of core.auth
        import importlib
        if "core.auth" in sys.modules:
            del sys.modules["core.auth"]

        import core.auth as auth_mod
        assert auth_mod.HAS_FCNTL is False
        assert auth_mod.fcntl is None

        # Construct an AuthManager — it must not crash.
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = auth_mod.AuthManager(str(Path(tmp) / "auth.json"))
            # The lock context manager must yield without error.
            with mgr._interprocess_auth_lock():
                pass

    def test_secret_storage_key_creation_thread_safe(self, tmp_path, monkeypatch):
        """Two threads racing into _get_fernet() on a fresh data dir must
        both receive the same valid Fernet key without exceptions."""
        import src.secret_storage as ss
        import threading

        tmp_key = tmp_path / ".app_key"
        monkeypatch.setattr(ss, "_KEY_PATH", tmp_key)
        monkeypatch.setattr(ss, "_fernet", None)

        results = {}
        errors = []
        barrier = threading.Barrier(2, timeout=5)

        def get_key(idx):
            try:
                barrier.wait()
                f = ss._get_fernet()
                results[idx] = f
            except Exception as e:
                errors.append((idx, e))

        t0 = threading.Thread(target=get_key, args=(0,))
        t1 = threading.Thread(target=get_key, args=(1,))
        t0.start()
        t1.start()
        t0.join()
        t1.join()

        assert not errors, f"Unexpected errors in thread race: {errors}"
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"

        f0, f1 = results[0], results[1]
        # Both must be usable Fernet instances that encrypt/decrypt compatibly.
        token = f0.encrypt(b"hello")
        assert f1.decrypt(token) == b"hello"
        token = f1.encrypt(b"world")
        assert f0.decrypt(token) == b"world"

    def test_create_user_survives_concurrent_set_oidc_user_admin(self, tmp_path):
        """create_user() (password user) must not be lost when a concurrent
        set_oidc_user_admin() executes on another manager.  Both operations
        now take the inter-process lock, so the second operation must reload
        and see the first operation's result."""
        from core.auth import AuthManager
        import threading

        auth_path = str(tmp_path / "auth.json")

        # Manager A: create an OIDC user (alice) then make her admin so the
        # no-op short-circuit in set_oidc_user_admin doesn't trigger.
        mgr_a = AuthManager(auth_path)
        alice = mgr_a.create_user_oidc("alice", "sub-a", "https://idp.example.com")
        assert alice == "alice"
        mgr_a._config["users"]["alice"]["is_admin"] = True
        mgr_a._save()

        # Manager B: a separate instance with the same auth file.
        mgr_b = AuthManager(auth_path)

        results = {}
        barrier = threading.Barrier(2, timeout=5)

        def do_create_user():
            barrier.wait()
            ok = mgr_a.create_user("bob", "password123")
            results["create"] = ok

        def do_sync_admin():
            barrier.wait()
            ok = mgr_b.set_oidc_user_admin("alice", False)
            results["sync"] = ok

        t_create = threading.Thread(target=do_create_user)
        t_sync = threading.Thread(target=do_sync_admin)
        t_create.start()
        t_sync.start()
        t_create.join()
        t_sync.join()

        assert results.get("create") is True, "create_user must succeed"
        assert results.get("sync") is True, "set_oidc_user_admin must succeed"

        # Reload both — bob and alice must both exist.
        mgr_a._load()
        mgr_b._load()
        users_a = mgr_a._config.get("users", {})
        users_b = mgr_b._config.get("users", {})
        assert "bob" in users_a, f"bob must survive concurrent admin sync; users: {list(users_a)}"
        assert "bob" in users_b, f"bob must survive concurrent admin sync; users: {list(users_b)}"
        assert "alice" in users_a
        assert "alice" in users_b
        assert not users_a["alice"].get("is_admin"), "alice must be demoted"


# ---------------------------------------------------------------------------
# OIDC/TOTP defense-in-depth
# ---------------------------------------------------------------------------


def test_check_oidc_totp_returns_false_for_normal_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("alice", "password123")
    assert mgr.check_oidc_totp("alice") is False


def test_check_oidc_totp_returns_true_for_oidc_user_with_totp(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")
    mgr._config["users"]["alice"]["totp_enabled"] = True
    mgr._save()

    # check_oidc_totp reloads auth.json, so this reflects an externally
    # persisted mutation rather than only the in-memory dictionary.
    assert mgr.check_oidc_totp("alice") is True


def test_check_oidc_totp_returns_false_for_non_oidc_user_with_totp(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("alice", "password123")
    mgr._config["users"]["alice"]["totp_enabled"] = True
    mgr._save()
    assert mgr.check_oidc_totp("alice") is False
