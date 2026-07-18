"""Regression: sessions issued by one uvicorn worker must validate on another.

The OIDC callback (or a password login) can run on worker A while the
browser's next request lands on worker B.  Each worker loads sessions.json
only at startup, so without the read-through reload in
AuthManager._reload_sessions_if_changed the new token would be rejected
and a successful login would immediately become a logged-out session.
"""

import importlib
import sys
import types
from pathlib import Path

from tests.helpers.import_state import clear_module


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


def _auth_module():
    _real_core_package()
    return importlib.import_module("core.auth")


def _two_workers(tmp_path):
    """Build two AuthManager instances over the same data directory,
    simulating two uvicorn worker processes."""
    auth_mod = _auth_module()
    auth_mod._hash_password = lambda password: f"hash:{password}"
    auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    auth_path = str(tmp_path / "auth.json")
    worker_a = auth_mod.AuthManager(auth_path)
    assert worker_a.create_user("alice", "password-1", is_admin=False)
    worker_b = auth_mod.AuthManager(auth_path)  # boots after user exists
    return worker_a, worker_b


class TestCrossWorkerSessions:
    def test_session_issued_on_other_worker_validates(self, tmp_path):
        worker_a, worker_b = _two_workers(tmp_path)
        token = worker_a.create_session_trusted("alice")
        assert token is not None
        # Worker B has never seen this token in memory — it must read
        # through to sessions.json and accept it.
        assert worker_b.validate_token(token) is True
        assert worker_b.get_username_for_token(token) == "alice"

    def test_unknown_token_still_rejected(self, tmp_path):
        worker_a, worker_b = _two_workers(tmp_path)
        worker_a.create_session_trusted("alice")
        assert worker_b.validate_token("f" * 64) is False
        assert worker_b.get_username_for_token("f" * 64) is None

    def test_expired_session_from_other_worker_rejected(self, tmp_path):
        auth_mod = _auth_module()
        worker_a, worker_b = _two_workers(tmp_path)
        token = worker_a.create_session_trusted("alice")
        # Force the persisted expiry into the past, as another worker
        # would see it after the TTL elapsed.
        with worker_a._sessions_lock:
            worker_a._sessions[token]["expiry"] = 1.0
        worker_a._save_sessions()
        assert worker_b.validate_token(token) is False
        assert worker_b.get_username_for_token(token) is None

    def test_reload_is_additive_not_destructive(self, tmp_path):
        """A reload must never drop tokens this worker already holds in
        memory (e.g. one issued moments ago, racing its own save)."""
        worker_a, worker_b = _two_workers(tmp_path)
        token_b = worker_b.create_session_trusted("alice")
        token_a = worker_a.create_session_trusted("alice")
        # B validating A's token triggers a reload; B's own token survives.
        assert worker_b.validate_token(token_a) is True
        assert worker_b.validate_token(token_b) is True


class TestCrossWorkerRevocation:
    def test_revocation_propagates_to_other_worker(self, tmp_path):
        """Logout on worker A must invalidate the token on worker B even
        though B holds it in its in-memory map."""
        worker_a, worker_b = _two_workers(tmp_path)
        token = worker_a.create_session_trusted("alice")
        assert worker_b.validate_token(token) is True  # B now caches it
        worker_a.revoke_token(token)
        assert worker_b.validate_token(token) is False
        assert worker_b.get_username_for_token(token) is None

    def test_revoke_user_sessions_propagates(self, tmp_path):
        """Admin-driven revocation (password change, user deletion) on one
        worker must take effect on the others."""
        worker_a, worker_b = _two_workers(tmp_path)
        token = worker_a.create_session_trusted("alice")
        assert worker_b.validate_token(token) is True
        assert worker_a.revoke_user_sessions("alice") == 1
        assert worker_b.validate_token(token) is False

    def test_never_persisted_token_survives_reload(self, tmp_path):
        """A token in memory that was never written to disk (racing its own
        save) must not be dropped when a reload observes another worker's
        write that lacks it."""
        worker_a, worker_b = _two_workers(tmp_path)
        import time as _time
        phantom = "e" * 64
        with worker_b._sessions_lock:
            worker_b._sessions[phantom] = {
                "username": "alice", "expiry": _time.time() + 3600,
            }
        token_a = worker_a.create_session_trusted("alice")  # bumps mtime
        assert worker_b.validate_token(token_a) is True  # triggers reload
        assert worker_b.validate_token(phantom) is True  # survived


class TestSecretFilePermissions:
    def test_sessions_file_owner_only(self, tmp_path):
        import stat
        worker_a, _ = _two_workers(tmp_path)
        worker_a.create_session_trusted("alice")
        mode = stat.S_IMODE((tmp_path / "sessions.json").stat().st_mode)
        assert mode == 0o600

    def test_auth_file_owner_only(self, tmp_path):
        import stat
        _two_workers(tmp_path)
        mode = stat.S_IMODE((tmp_path / "auth.json").stat().st_mode)
        assert mode == 0o600

    def test_preexisting_world_readable_files_restricted_on_load(self, tmp_path):
        """Files written before the 0600 policy get restricted at startup."""
        import stat
        auth_mod = _auth_module()
        auth_mod._hash_password = lambda password: f"hash:{password}"
        auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
        auth_path = str(tmp_path / "auth.json")
        mgr = auth_mod.AuthManager(auth_path)
        assert mgr.create_user("alice", "password-1", is_admin=False)
        mgr.create_session_trusted("alice")
        (tmp_path / "auth.json").chmod(0o644)
        (tmp_path / "sessions.json").chmod(0o644)
        auth_mod.AuthManager(auth_path)  # fresh load restricts both
        assert stat.S_IMODE((tmp_path / "auth.json").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "sessions.json").stat().st_mode) == 0o600
