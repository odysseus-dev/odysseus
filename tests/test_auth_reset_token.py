"""Unit tests for AuthManager reset-token primitives (TST-P5-01).

Covers:
- generate_reset_token / consume_reset_token / admin_set_password
- Happy path: valid token sets the new password (single use)
- Single-use: token is rejected on replay
- TTL expiry: expired token is rejected
- Invalid token: unknown token is rejected
- admin_set_password happy path + non-admin rejection
"""

import importlib
import json
import sys
import time
import types
from pathlib import Path

import pytest

from tests.helpers.import_state import clear_module


# ---------------------------------------------------------------------------
# Module / fixture helpers
# ---------------------------------------------------------------------------

def _real_core_package():
    """Ensure the real core package is available and core.auth will reload
    from disk rather than from a previously-installed stub."""
    root = Path(__file__).resolve().parent.parent
    core_path = str(root / "core")
    core = sys.modules.get("core")
    if core is None:
        core = types.ModuleType("core")
        sys.modules["core"] = core
    core.__path__ = [core_path]
    clear_module("core.auth")
    return core


def _auth_module():
    _real_core_package()
    return importlib.import_module("core.auth")


def _make_manager(tmp_path):
    """Create a fresh AuthManager with a lightweight bcrypt stub."""
    auth_mod = _auth_module()
    # Stub bcrypt so tests don't spend time on real hashing.
    auth_mod._hash_password = lambda password: f"hash:{password}"
    auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    auth_path = tmp_path / "auth.json"
    mgr = auth_mod.AuthManager(str(auth_path))
    assert mgr.create_user("alice", "old-password", is_admin=False)
    assert mgr.create_user("admin", "admin-pw", is_admin=True)
    return mgr


# ---------------------------------------------------------------------------
# generate_reset_token
# ---------------------------------------------------------------------------

class TestGenerateResetToken:
    def test_returns_token_for_known_user(self, tmp_path):
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        assert isinstance(token, str)
        assert len(token) > 16

    def test_returns_none_for_unknown_user(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.generate_reset_token("nobody") is None

    def test_new_request_replaces_prior_token(self, tmp_path):
        mgr = _make_manager(tmp_path)
        first = mgr.generate_reset_token("alice")
        second = mgr.generate_reset_token("alice")
        assert first != second
        # The first token must have been invalidated.
        assert mgr.consume_reset_token(first, "NewPassword1!") is False
        # Only the second token is valid.
        assert mgr.consume_reset_token(second, "NewPassword1!") is True


# ---------------------------------------------------------------------------
# consume_reset_token — happy path
# ---------------------------------------------------------------------------

class TestConsumeResetTokenHappyPath:
    def test_valid_token_sets_new_password(self, tmp_path):
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        result = mgr.consume_reset_token(token, "newpassword1")
        assert result is True
        # The user can now log in with the new password.
        assert mgr.verify_password("alice", "newpassword1") is True

    def test_valid_token_revokes_active_sessions(self, tmp_path):
        mgr = _make_manager(tmp_path)
        session = mgr.create_session("alice", "old-password")
        assert mgr.validate_token(session) is True
        token = mgr.generate_reset_token("alice")
        mgr.consume_reset_token(token, "newpassword1")
        assert mgr.validate_token(session) is False


# ---------------------------------------------------------------------------
# consume_reset_token — single-use (replay rejection)
# ---------------------------------------------------------------------------

class TestConsumeResetTokenSingleUse:
    def test_token_rejected_on_replay(self, tmp_path):
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        assert mgr.consume_reset_token(token, "newpassword1") is True
        # Second attempt with the same token must fail.
        assert mgr.consume_reset_token(token, "anotherpassword1") is False
        # Password must still be the one set on first consumption.
        assert mgr.verify_password("alice", "newpassword1") is True
        assert mgr.verify_password("alice", "anotherpassword1") is False


# ---------------------------------------------------------------------------
# consume_reset_token — TTL expiry
# ---------------------------------------------------------------------------

class TestConsumeResetTokenExpiry:
    def test_expired_token_rejected(self, tmp_path):
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        # Directly expire the token in the in-memory store (past TTL).
        with mgr._reset_lock:
            mgr._reset_tokens[token]["expiry"] = time.time() - 1
        result = mgr.consume_reset_token(token, "newpassword1")
        assert result is False
        # The token should have been pruned by consume_reset_token.
        assert token not in mgr._reset_tokens
        # Original password must still work.
        assert mgr.verify_password("alice", "old-password") is True


# ---------------------------------------------------------------------------
# consume_reset_token — invalid token
# ---------------------------------------------------------------------------

class TestConsumeResetTokenInvalidToken:
    def test_unknown_token_rejected(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.consume_reset_token("not-a-real-token", "newpassword1") is False

    def test_empty_token_rejected(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.consume_reset_token("", "newpassword1") is False

    def test_short_password_rejected_before_token_lookup(self, tmp_path):
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        assert mgr.consume_reset_token(token, "short") is False
        # Token must still be valid after a rejected short-password attempt.
        assert mgr.consume_reset_token(token, "validlongpassword") is True


# ---------------------------------------------------------------------------
# admin_set_password
# ---------------------------------------------------------------------------

class TestAdminSetPassword:
    def test_admin_can_set_password_for_other_user(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = mgr.admin_set_password("admin", "alice", "brandnewpass1")
        assert result is True
        assert mgr.verify_password("alice", "brandnewpass1") is True
        assert mgr.verify_password("alice", "old-password") is False

    def test_admin_set_password_revokes_sessions(self, tmp_path):
        mgr = _make_manager(tmp_path)
        session = mgr.create_session("alice", "old-password")
        assert mgr.validate_token(session) is True
        mgr.admin_set_password("admin", "alice", "brandnewpass1")
        assert mgr.validate_token(session) is False

    def test_non_admin_cannot_set_password(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = mgr.admin_set_password("alice", "admin", "hijacked!")
        assert result is False
        # Admin password must be unchanged.
        assert mgr.verify_password("admin", "admin-pw") is True

    def test_admin_set_password_unknown_target_returns_false(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.admin_set_password("admin", "nobody", "newpass1") is False


# ---------------------------------------------------------------------------
# prune_reset_tokens (REL-P5-001)
# ---------------------------------------------------------------------------

class TestPruneResetTokens:
    def test_prune_removes_expired_entries(self, tmp_path):
        import core.auth as auth_mod
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        # Manually expire the token in the dict.
        mgr._reset_tokens[token]["expiry"] = time.time() - 1
        count = mgr.prune_reset_tokens()
        assert count == 1
        assert token not in mgr._reset_tokens

    def test_prune_preserves_valid_entries(self, tmp_path):
        mgr = _make_manager(tmp_path)
        token = mgr.generate_reset_token("alice")
        count = mgr.prune_reset_tokens()
        assert count == 0
        assert token in mgr._reset_tokens
