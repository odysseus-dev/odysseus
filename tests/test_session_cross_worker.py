"""Regression: sessions issued by one uvicorn worker must validate on another.

The OIDC callback (or a password login) can run on worker A while the
browser's next request lands on worker B.  Each worker loads sessions.json
only at startup, so without the read-through reload in
AuthManager._reload_sessions_if_changed the new token would be rejected
and a successful login would immediately become a logged-out session.
"""

import contextlib
import importlib
import json
import logging
import os
import sys
import time
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


# ---------------------------------------------------------------------------
# Cross-worker auth/user snapshot sync (PR #5752 review P1, r4347312512).
#
# The session-file reload imports cookies issued by another worker, but the
# auth/user snapshot used by the orphan check stays stale for that worker's
# lifetime unless it is refreshed too.  If worker A creates an account after
# worker B starts, B must treat A's valid cookie as a live account — not as a
# deleted user — and must not persist a revocation tombstone that later makes
# A reject its own cookie.
# ---------------------------------------------------------------------------


def _fresh_workers(tmp_path):
    """Two AuthManagers that both boot before the account in question exists.

    Seeds an unrelated admin ("rootadmin") before any worker boots, so every
    later account creation is invisible to every already-booted worker.
    Mirrors the reviewer's setup: worker B starts with a stale auth.json
    snapshot and must hot-reload it on validation.
    """
    auth_mod = _auth_module()
    auth_mod._hash_password = lambda password: f"hash:{password}"
    auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    auth_path = str(tmp_path / "auth.json")
    seed = auth_mod.AuthManager(auth_path)
    assert seed.create_user("rootadmin", "seed-pw", is_admin=True)
    worker_a = auth_mod.AuthManager(auth_path)
    worker_b = auth_mod.AuthManager(auth_path)
    return auth_mod, worker_a, worker_b, auth_path


def _workers_with_user(tmp_path, username):
    """Two AuthManagers where *username* exists before either worker boots.

    For guards whose subject (deletion, steady state) is independent of the
    new-account race.
    """
    auth_mod = _auth_module()
    auth_mod._hash_password = lambda password: f"hash:{password}"
    auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    auth_path = str(tmp_path / "auth.json")
    seed = auth_mod.AuthManager(auth_path)
    assert seed.create_user("rootadmin", "seed-pw", is_admin=True)
    worker_a = auth_mod.AuthManager(auth_path)
    assert worker_a.create_user(username, "pw-" + username, is_admin=False)
    worker_b = auth_mod.AuthManager(auth_path)
    return auth_mod, worker_a, worker_b, auth_path


def _stub_core_database(monkeypatch):
    """Route the ApiToken purge in delete_user/set_admin to a throwaway stub
    so tests never touch the real sqlite database."""

    class _StubQuery:
        def filter(self, *conds):
            return self

        def delete(self):
            return 0

    class _StubSession:
        def query(self, model):
            return _StubQuery()

    @contextlib.contextmanager
    def _stub_db_session():
        yield _StubSession()

    stub = types.ModuleType("core.database")
    stub.get_db_session = _stub_db_session
    stub.ApiToken = type("ApiToken", (), {"owner": None})
    monkeypatch.setitem(sys.modules, "core.database", stub)


class TestCrossWorkerAuthSnapshotSync:
    def test_new_account_validates_on_worker_b_after_both_boot(self, tmp_path):
        """Reviewer reproduction, exactly as reported: both workers start
        before the account exists; A creates the user and a session; B
        validates; then A validates; the token stays in sessions.json."""
        _, worker_a, worker_b, _ = _fresh_workers(tmp_path)
        assert worker_a.create_user("carol", "pw-carol", is_admin=False)
        token = worker_a.create_session_trusted("carol")
        assert token is not None
        assert worker_b.validate_token(token) is True
        # The reviewer's second step: after B rejects, A must keep accepting
        # its own cookie (B's rejection used to persist a tombstone that
        # removed the token from disk, making A reject it too).
        assert worker_a.validate_token(token) is True
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token in persisted

    def test_new_oidc_account_validates_on_worker_b_after_both_boot(self, tmp_path):
        """Same as above, but the account comes from create_user_oidc — the
        flow the P1 was about."""
        _, worker_a, worker_b, _ = _fresh_workers(tmp_path)
        created = worker_a.create_user_oidc(
            "oidcuser", "sub-7", "https://issuer.example", "oidc@example.example",
        )
        assert created == "oidcuser"
        token = worker_a.create_session_trusted("oidcuser")
        assert token is not None
        assert worker_b.validate_token(token) is True
        assert worker_a.validate_token(token) is True
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token in persisted

    def test_new_account_validates_on_b_via_get_username(self, tmp_path):
        """The same account-created-later race through
        get_username_for_token (app.py calls it on every authenticated
        request, right after validate_token)."""
        _, worker_a, worker_b, _ = _fresh_workers(tmp_path)
        assert worker_a.create_user("carol", "pw-carol", is_admin=False)
        token = worker_a.create_session_trusted("carol")
        assert worker_b.get_username_for_token(token) == "carol"
        assert worker_a.get_username_for_token(token) == "carol"
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token in persisted

    def test_account_created_during_b_reload_is_accepted(self, tmp_path):
        """Deterministic interleave: A finishes creating the user and the
        session at the moment B is about to reload its sessions file, i.e.
        after B has already refreshed users but before it looks up."""
        _, worker_a, worker_b, _ = _fresh_workers(tmp_path)
        token = "ab" * 32
        original_reload = worker_b._reload_sessions_if_changed
        interleaved = []

        def reload_with_interleave():
            # A's create+save lands after B's user refresh and before its
            # session reload reads the file — once, on B's first validation.
            if not interleaved:
                interleaved.append(True)
                assert worker_a.create_user("dana", "pw-dana", is_admin=False)
                with worker_a._sessions_lock:
                    worker_a._sessions[token] = {
                        "username": "dana", "expiry": time.time() + 3600,
                    }
                worker_a._save_sessions()
            original_reload()

        worker_b._reload_sessions_if_changed = reload_with_interleave
        try:
            assert worker_b.validate_token(token) is True
            assert worker_b.get_username_for_token(token) == "dana"
            persisted = json.loads((tmp_path / "sessions.json").read_text())
            assert token in persisted
        finally:
            worker_b._reload_sessions_if_changed = original_reload

    def test_deleted_user_rejected_on_both_paths_and_token_stays_gone(
        self, tmp_path, monkeypatch
    ):
        """Genuinely deleted users must still be revoked on the other
        worker, and the tombstone must keep the token off disk."""
        _stub_core_database(monkeypatch)
        _, worker_a, worker_b, _ = _workers_with_user(tmp_path, "erin")
        token = worker_a.create_session_trusted("erin")
        assert worker_b.validate_token(token) is True
        assert worker_a.delete_user("erin", "rootadmin") is True
        assert worker_b.validate_token(token) is False
        assert worker_b.get_username_for_token(token) is None
        assert worker_b.validate_token(token) is False
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token not in persisted

    def test_unreadable_auth_snapshot_rejects_without_revoking_then_recovers(
        self, tmp_path
    ):
        """A corrupt auth.json during B's sync must not be mistaken for a
        deleted user: reject this request, keep the last good snapshot,
        and do NOT write a revocation. Accept once the file is restored."""
        _, worker_a, worker_b, auth_path = _fresh_workers(tmp_path)
        assert worker_a.create_user("frank", "pw-frank", is_admin=False)
        token = worker_a.create_session_trusted("frank")
        good = Path(auth_path).read_text()
        Path(auth_path).write_text("{ this is not json ]")
        assert worker_b.validate_token(token) is False
        assert worker_b.get_username_for_token(token) is None
        # Snapshot not emptied...
        assert "rootadmin" in worker_b.users
        # ...and no revocation persisted: the token is still on disk.
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token in persisted
        Path(auth_path).write_text(good)
        assert worker_b.validate_token(token) is True
        assert worker_b.get_username_for_token(token) == "frank"
        assert worker_a.validate_token(token) is True

    def test_rename_on_a_moves_b_cached_session_to_new_name(self, tmp_path):
        """A rename on worker A must resolve B's cached session to the new
        name; B's adopted name must survive so both workers keep the token."""
        _, worker_a, worker_b, _ = _fresh_workers(tmp_path)
        assert worker_a.create_user("gwen", "pw-gwen", is_admin=False)
        token = worker_a.create_session_trusted("gwen")
        assert worker_b.get_username_for_token(token) == "gwen"
        assert worker_a.rename_user("gwen", "wendy", "rootadmin") is True
        assert worker_b.get_username_for_token(token) == "wendy"
        assert worker_a.get_username_for_token(token) == "wendy"
        assert worker_b.validate_token(token) is True
        assert worker_a.validate_token(token) is True
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token in persisted
        assert persisted[token]["username"] == "wendy"

    def test_rename_on_a_renames_token_only_worker_b_issued(self, tmp_path):
        """Rename must pull the sessions file from disk (ungated reload)
        so tokens issued on B that A has never seen get renamed too."""
        _, worker_a, worker_b, _ = _workers_with_user(tmp_path, "hugo")
        token = worker_b.create_session_trusted("hugo")
        assert worker_a.rename_user("hugo", "hector", "rootadmin") is True
        assert worker_b.get_username_for_token(token) == "hector"
        assert worker_a.get_username_for_token(token) == "hector"
        assert worker_b.validate_token(token) is True
        assert worker_a.validate_token(token) is True
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token in persisted
        assert persisted[token]["username"] == "hector"

    def test_hot_reload_does_not_admit_hand_edited_reserved_username(self, tmp_path):
        """A hand-edited sentinel row in auth.json must be dropped in
        memory on hot reload (and not admitted), without rewriting the
        file."""
        _, worker_a, worker_b, auth_path = _fresh_workers(tmp_path)
        data = json.loads(Path(auth_path).read_text())
        data["users"]["internal-tool"] = {
            "password_hash": None, "created": time.time(), "is_admin": True,
        }
        Path(auth_path).write_text(json.dumps(data))
        token = "cd" * 32
        with worker_a._sessions_lock:
            worker_a._sessions[token] = {
                "username": "internal-tool", "expiry": time.time() + 3600,
            }
        worker_a._save_sessions()
        assert worker_b.validate_token(token) is False
        assert worker_b.get_username_for_token(token) is None
        assert worker_a.validate_token(token) is False
        assert "internal-tool" not in worker_a.users
        assert "internal-tool" not in worker_b.users
        # The drop is in-memory only: validator paths must not write auth.json.
        assert "internal-tool" in json.loads(Path(auth_path).read_text())["users"]
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert token not in persisted

    def test_demotion_on_a_visible_in_b_after_next_validation(
        self, tmp_path, monkeypatch
    ):
        """A demotion on A flips B's is_admin once B validates again; the
        same sync must happen through get_username_for_token on a worker
        whose snapshot was not refreshed by the other path."""
        _stub_core_database(monkeypatch)
        auth_mod, worker_a, worker_b, auth_path = _fresh_workers(tmp_path)
        assert worker_a.create_user("ivy", "pw-ivy", is_admin=True)
        token_ivy = worker_a.create_session_trusted("ivy")
        assert worker_b.validate_token(token_ivy) is True
        assert worker_b.is_admin("ivy") is True
        assert worker_a.set_admin("ivy", False, "rootadmin") is auth_mod.SetAdminResult.OK
        assert worker_b.validate_token(token_ivy) is True
        assert worker_b.is_admin("ivy") is False
        # Independent snapshot for the second path, so a missing user
        # refresh in either path is observable on its own account.
        assert worker_a.create_user("jack", "pw-jack", is_admin=True)
        token_jack = worker_a.create_session_trusted("jack")
        worker_c = auth_mod.AuthManager(auth_path)
        assert worker_c.get_username_for_token(token_jack) == "jack"
        assert worker_c.is_admin("jack") is True
        assert worker_a.set_admin("jack", False, "rootadmin") is auth_mod.SetAdminResult.OK
        assert worker_c.get_username_for_token(token_jack) == "jack"
        assert worker_c.is_admin("jack") is False
        assert worker_c.validate_token(token_jack) is True

    def test_steady_state_validations_reparse_neither_file(self, tmp_path, monkeypatch):
        """Signature/mtime gates keep steady-state cost at one stat each:
        no json.load for either file across repeated validations, including
        after this worker's own auth write (which must self-record)."""
        _, worker_a, worker_b, _ = _workers_with_user(tmp_path, "kate")
        token = worker_a.create_session_trusted("kate")
        assert worker_b.validate_token(token) is True  # drain the session gate
        # B writes auth.json itself: its own signature must be recorded.
        assert worker_b.create_user("liam", "pw-liam", is_admin=False)
        calls = []
        real_load = json.load

        def counting_load(*args, **kwargs):
            calls.append(1)
            return real_load(*args, **kwargs)

        monkeypatch.setattr(json, "load", counting_load)
        for _ in range(3):
            assert worker_b.validate_token(token) is True
            assert worker_b.get_username_for_token(token) == "kate"
        assert calls == [], f"parsed {len(calls)} times in steady state"

    def test_rename_then_unrelated_b_save_keeps_new_name_on_disk(self, tmp_path):
        """Finding 1 (high): a rename on A must not become a logout on both
        workers when B, holding a stale snapshot, writes sessions for an
        unrelated reason before it validates anything. B used to write the old
        username back to disk, then tombstone the token it reverted itself."""
        _, worker_a, worker_b, _ = _workers_with_user(tmp_path, "gwen")
        token = worker_a.create_session_trusted("gwen")
        assert worker_b.get_username_for_token(token) == "gwen"  # B caches gwen
        assert worker_a.rename_user("gwen", "wendy", "rootadmin") is True
        sp = tmp_path / "sessions.json"
        assert json.loads(sp.read_text())[token]["username"] == "wendy"
        # B issues an unrelated login before handling any authenticated
        # request. Its _save_sessions merge must not revert wendy to gwen.
        assert worker_b.create_session_trusted("rootadmin") is not None
        persisted = json.loads(sp.read_text())
        assert persisted.get(token, {}).get("username") == "wendy"
        assert worker_b.get_username_for_token(token) == "wendy"
        assert worker_a.get_username_for_token(token) == "wendy"
        assert worker_b.validate_token(token) is True
        assert worker_a.validate_token(token) is True
        assert token in json.loads(sp.read_text())

    def test_own_rename_survives_merge_against_stale_old_name_disk(self, tmp_path):
        """This worker's own rename must win a merge against a disk entry that
        still holds the old name — the locally-renamed-token set, not the user
        snapshot, is what keeps disk-win from undoing the rename actor's own
        write."""
        _, worker_a, worker_b, _ = _workers_with_user(tmp_path, "gwen")
        # B (not A) issues the session; A renamed gwen on disk first.
        token = worker_b.create_session_trusted("gwen")
        assert worker_a.rename_user("gwen", "wendy", "rootadmin") is True
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert persisted[token]["username"] == "wendy"
        assert worker_a.get_username_for_token(token) == "wendy"
        assert worker_b.get_username_for_token(token) == "wendy"
        assert worker_a.validate_token(token) is True

    def test_double_rename_across_workers_survives_unrelated_save(self, tmp_path):
        """Round-2 review (Y2): the pending-rename guard lasts only *since
        this worker's last disk sync*.  A renames gwen->wendy and its save
        succeeds, B renames wendy->zoe (disk now says zoe), then A makes an
        unrelated session save.  If A's save never cleared the token from
        its pending-rename set, A would write its stale 'wendy' back over
        zoe and both workers would lose the session.  Disk must stay zoe
        and both workers must resolve zoe."""
        _, worker_a, worker_b, _ = _workers_with_user(tmp_path, "gwen")
        token = worker_a.create_session_trusted("gwen")
        assert worker_b.get_username_for_token(token) == "gwen"  # both cache
        assert worker_a.rename_user("gwen", "wendy", "rootadmin") is True
        assert worker_b.get_username_for_token(token) == "wendy"  # B sees wendy
        assert worker_b.rename_user("wendy", "zoe", "rootadmin") is True
        assert json.loads((tmp_path / "sessions.json").read_text())[token][
            "username"
        ] == "zoe"
        # A's unrelated login: its _save_sessions merge must take zoe from
        # disk — A's rename is long synced, not pending.
        assert worker_a.create_session_trusted("rootadmin") is not None
        persisted = json.loads((tmp_path / "sessions.json").read_text())
        assert persisted.get(token, {}).get("username") == "zoe"
        assert worker_a.get_username_for_token(token) == "zoe"
        assert worker_b.get_username_for_token(token) == "zoe"
        assert worker_a.validate_token(token) is True
        assert worker_b.validate_token(token) is True

    def test_second_chance_ungated_reload_picks_up_cross_worker_change(
        self, tmp_path
    ):
        """Finding 2 (X1): the second chance must force an ungated session
        reload. Pin the disk mtime to B's recorded value so the mtime-gated
        reload misses the cross-worker change; only the ungated reload adopts
        the renamed username from disk."""
        _, worker_a, worker_b, _ = _fresh_workers(tmp_path)
        assert worker_a.create_user("gwen", "pw-gwen", is_admin=False)
        token = worker_a.create_session_trusted("gwen")
        assert worker_b.validate_token(token) is True  # B syncs gwen token
        assert worker_a.rename_user("gwen", "wendy", "rootadmin") is True
        sp = tmp_path / "sessions.json"
        st = sp.stat()
        # Pin sessions.json mtime to B's recorded gate value.
        os.utime(sp, ns=(st.st_atime_ns, worker_b._sessions_mtime_ns))
        assert worker_b.get_username_for_token(token) == "wendy"

    def test_boot_first_validation_reparses_neither_file(self, tmp_path, monkeypatch):
        """Finding 3 (X9): _load records the auth.json signature, so a fresh
        worker's first validation re-parses neither file (boot parse excepted,
        installed spy-wise after construction)."""
        _, worker_a, _, auth_path = _workers_with_user(tmp_path, "kate")
        token = worker_a.create_session_trusted("kate")
        auth_mod = _auth_module()
        fresh = auth_mod.AuthManager(auth_path)  # boot parses before the spy
        calls = []
        real_load = json.load

        def counting_load(*args, **kwargs):
            calls.append(1)
            return real_load(*args, **kwargs)

        monkeypatch.setattr(json, "load", counting_load)
        assert fresh.validate_token(token) is True
        assert fresh.get_username_for_token(token) == "kate"
        assert calls == [], f"fresh worker re-parsed {len(calls)} time(s)"

    def test_second_chance_expiry_is_rejected_and_pruned(self, tmp_path):
        """Finding 4 (X14): an expired token reached via the second chance
        (the ungated reload adopts the stale-expired disk entry over B's
        stale live mapping) must be rejected and pruned from sessions.json."""
        _, _, _, auth_path = _workers_with_user(tmp_path, "gwen")
        sp = tmp_path / "sessions.json"
        token = "b0" * 32
        auth_mod = _auth_module()
        worker_b = auth_mod.AuthManager(auth_path)
        assert "gwen" in worker_b.users
        # Disk holds only a stale EXPIRED entry for gwen (a known user), and
        # this worker's mtime gate is already current for that file: the
        # gated reload will not see it (only the ungated force reload can).
        sp.write_text(json.dumps(
            {token: {"username": "gwen", "expiry": 1.0}}
        ))
        worker_b._sessions_mtime_ns = sp.stat().st_mtime_ns
        # The same token held in memory under an unknown user, unexpired:
        # the first lookup is an orphan, not an expiry.
        with worker_b._sessions_lock:
            worker_b._sessions[token] = {
                "username": "ghost", "expiry": time.time() + 3600,
            }
        assert worker_b.validate_token(token) is False
        assert worker_b.get_username_for_token(token) is None
        assert token not in json.loads(sp.read_text())
        assert token not in worker_b._sessions

    def test_missing_auth_json_is_quiet_and_fails_closed(self, tmp_path, caplog):
        """Finding 6: a missing auth.json must be a quiet unconfirmed state —
        no ERROR log per validation, and still failing closed without
        revoking the session."""
        _, _, worker_b, auth_path = _fresh_workers(tmp_path)
        os.remove(auth_path)
        token = "fe" * 32
        with worker_b._sessions_lock:
            worker_b._sessions[token] = {
            "username": "ghost-user", "expiry": time.time() + 3600,
            }
        with caplog.at_level(logging.DEBUG, logger="core.auth"):
            assert worker_b.validate_token(token) is False
            assert worker_b.get_username_for_token(token) is None
        errors = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.ERROR and "auth config" in r.getMessage()
        ]
        assert not errors, f"missing-file validations logged ERROR: {errors}"
        # Fails closed: unauthenticated, but the session is not revoked...
        with worker_b._sessions_lock:
            assert token in worker_b._sessions
        # ...and the last-good snapshot is intact.
        assert "rootadmin" in worker_b.users

    def test_reset_password_save_leaves_signature_unconfirmed(
        self, tmp_path, monkeypatch
    ):
        """Finding 5: reset_user_password writes auth.json without the
        inter-process auth lock, so its _save must leave the snapshot
        unconfirmed — the writing worker's next refresh re-reads auth.json."""
        _, worker_a, _, _ = _workers_with_user(tmp_path, "kate")
        assert worker_a.reset_user_password("kate", "new-pw", "rootadmin") is True
        calls = []
        real_load = json.load

        def counting_load(fp, *args, **kwargs):
            calls.append(os.path.basename(getattr(fp, "name", "")))
            return real_load(fp, *args, **kwargs)

        monkeypatch.setattr(json, "load", counting_load)
        assert worker_a.validate_token("no" * 32) is False
        assert "auth.json" in calls, "writing worker did not re-read auth.json"
