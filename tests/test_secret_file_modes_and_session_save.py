"""Secret-bearing files are owner-only from creation; revoked sessions stay revoked.

Two regressions pinned here:

* Files holding secrets were created world-readable. ``atomic_write_json``
  opened its temp file with plain ``open()`` (0o666 & ~umask, so 0o644 under the
  usual 022) and that is the file ``sessions.json`` (raw session tokens) and
  ``auth.json`` (TOTP secrets, backup codes) became. The vault config holding
  the Bitwarden session key was written with ``write_text`` and only then
  chmod'ed, and ``data/.app_key`` was generated with no exclusive create, so two
  first-start callers could each write their own key.
* ``AuthManager._save_sessions`` snapshotted the sessions under the lock but
  wrote outside it, so a save holding an older snapshot could land after a
  revocation and bring the revoked token back on the next restart.

The mode tests disable ``os.chmod``: a file that is only tightened after it
was written still exposes the secret in between, so they check the mode the
file was created with. Everything they write stays inside ``tmp_path``; none of
them runs git.
"""

import asyncio
import importlib
import json
import os
import stat
import threading

import pytest
from cryptography.fernet import Fernet

from core import atomic_io
from core import auth as auth_mod
from routes.vault import vault_routes
from src import tool_approval_scopes
from src.tools import vault as vault_tool

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


@pytest.fixture
def umask_022():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def no_chmod(monkeypatch):
    """Make any after-the-fact chmod a no-op so the creation mode is what's left."""
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------


@posix_only
def test_atomic_write_json_creates_owner_only_file_under_umask_022(tmp_path, umask_022):
    target = tmp_path / "sessions.json"

    atomic_io.atomic_write_json(str(target), {"raw-session-token": {"username": "alice"}})

    assert _mode(target) == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "raw-session-token": {"username": "alice"}
    }
    # Replacing an existing world-readable file leaves an owner-only one.
    target.chmod(0o644)
    atomic_io.atomic_write_json(str(target), {"users": {}}, indent=2)
    assert _mode(target) == 0o600


# ---------------------------------------------------------------------------
# Vault config (Bitwarden session key)
# ---------------------------------------------------------------------------


@posix_only
def test_vault_routes_save_config_creates_owner_only_file(tmp_path, monkeypatch, umask_022, no_chmod):
    vault_file = tmp_path / "data" / "vault.json"
    monkeypatch.setattr(vault_routes, "VAULT_FILE", vault_file)

    vault_routes._save_config({"owner": "alice", "session": "bw-session-key"})

    assert _mode(vault_file) == 0o600
    assert vault_routes._load_config() == {"owner": "alice", "session": "bw-session-key"}


@posix_only
def test_vault_unlock_tool_saves_session_owner_only(tmp_path, monkeypatch, umask_022, no_chmod):
    vault_file = tmp_path / "vault.json"
    monkeypatch.setattr(vault_tool, "VAULT_FILE", str(vault_file))

    async def fake_run_bw(args, session=None, input_text=None):
        return "bw-session-key", "", 0

    monkeypatch.setattr(vault_tool, "_run_bw", fake_run_bw)

    result = asyncio.run(vault_tool.do_vault_unlock(json.dumps({"master_password": "pw"})))

    assert result["exit_code"] == 0, result
    assert _mode(vault_file) == 0o600
    assert json.loads(vault_file.read_text(encoding="utf-8"))["session"] == "bw-session-key"


# ---------------------------------------------------------------------------
# data/.app_key
# ---------------------------------------------------------------------------


@pytest.fixture
def key_storage(tmp_path, monkeypatch):
    # Patch the module callers resolve now, not the one bound when this file
    # was imported: tests/test_security_regressions.py pops src.secret_storage
    # from sys.modules and re-imports it, and tool_approval_scopes._grant_key
    # imports it at call time, so a stale binding sent that caller to a
    # different, unpatched key file.
    ss = importlib.import_module("src.secret_storage")
    monkeypatch.setattr(ss, "_KEY_PATH", tmp_path / "data" / ".app_key")
    monkeypatch.setattr(ss, "_fernet", None)
    return ss


@posix_only
def test_concurrent_first_key_loads_share_one_owner_only_key(key_storage, monkeypatch, umask_022, no_chmod):
    ss = key_storage
    # The interleaving is forced with events, never with a timer: the first
    # loader is held right after it found no key file, and is released only
    # once the second loader has either found no key file as well (the race)
    # or is queued behind the first on the key lock (serialized). The waits
    # below are hang guards; neither path depends on how fast a thread runs.
    first_held = threading.Event()
    second_arrived = threading.Event()
    release = threading.Event()

    class _HeldFernet(Fernet):
        @staticmethod
        def generate_key():
            key = Fernet.generate_key()
            # Only reached after a loader saw no key file.
            (second_arrived if first_held.is_set() else first_held).set()
            release.wait(timeout=60)
            return key

    class _ObservedKeyLock:
        """The module's re-entrant lock, signalling when a caller must queue."""

        def __init__(self):
            self._lock = threading.RLock()

        def __enter__(self):
            if not self._lock.acquire(blocking=False):
                second_arrived.set()
                self._lock.acquire()
            return self

        def __exit__(self, *exc_info):
            self._lock.release()

    monkeypatch.setattr(ss, "Fernet", _HeldFernet)
    # raising=False: a module without the lock (the unfixed one) still runs.
    monkeypatch.setattr(ss, "_KEY_LOCK", _ObservedKeyLock(), raising=False)

    results = {}
    errors = []

    def loader(name, load):
        def run():
            try:
                results[name] = load()
            except BaseException as exc:  # surfaced by the assertion below
                errors.append(exc)

        return threading.Thread(target=run, daemon=True)

    first = loader("token", lambda: ss.encrypt("imap-password"))
    # The direct _load_or_create_key path tool approval signatures use.
    second = loader("grant_key", tool_approval_scopes._grant_key)
    first.start()
    try:
        assert first_held.wait(timeout=60), "first loader never reached key generation"
        second.start()
        assert second_arrived.wait(timeout=60), (
            "second loader neither reached key generation nor queued on the key lock"
        )
    finally:
        release.set()
        for thread in (first, second):
            if thread.ident is not None:
                thread.join(timeout=60)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []

    key_on_disk = ss._KEY_PATH.read_bytes()
    assert results["grant_key"] == key_on_disk
    assert _mode(ss._KEY_PATH) == 0o600
    # After a restart the secret encrypted on first start still decrypts.
    monkeypatch.setattr(ss, "_fernet", None)
    assert ss.decrypt(results["token"]) == "imap-password"


def test_key_created_by_another_process_mid_creation_is_adopted(key_storage, monkeypatch):
    ss = key_storage
    winner = Fernet.generate_key()

    class _RacedFernet(Fernet):
        @staticmethod
        def generate_key():
            # Another process creates the key after our "no key yet" check.
            ss._KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            ss._KEY_PATH.write_bytes(winner)
            return Fernet.generate_key()

    monkeypatch.setattr(ss, "Fernet", _RacedFernet)

    assert ss._load_or_create_key() == winner
    assert ss._KEY_PATH.read_bytes() == winner


# ---------------------------------------------------------------------------
# sessions.json: a stale save must not resurrect a revoked token
# ---------------------------------------------------------------------------


def test_revoke_racing_a_stale_session_save_leaves_token_off_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_mod, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(auth_mod, "_verify_password", lambda password, hashed: hashed == f"hash:{password}")
    auth_path = tmp_path / "auth.json"
    mgr = auth_mod.AuthManager(str(auth_path))
    assert mgr.create_user("alice", "alice-password", is_admin=False)
    assert mgr.create_user("bob", "bob-password", is_admin=False)
    revoked = mgr.create_session_trusted("alice")
    assert revoked in json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))

    real_write = auth_mod._atomic_write_json
    first_save_paused = threading.Event()
    release_first_save = threading.Event()
    paused_once = []

    def pausing_write(path, data, **kwargs):
        if path == mgr._sessions_path and not paused_once:
            paused_once.append(True)
            first_save_paused.set()
            release_first_save.wait(timeout=10)
        real_write(path, data, **kwargs)

    monkeypatch.setattr(auth_mod, "_atomic_write_json", pausing_write)
    issued = {}

    def issue_bob_session():
        # Its save snapshots the sessions while alice's token is still live.
        issued["bob"] = mgr.create_session_trusted("bob")

    stale_saver = threading.Thread(target=issue_bob_session)
    stale_saver.start()
    assert first_save_paused.wait(timeout=10)

    revoker = threading.Thread(target=mgr.revoke_token, args=(revoked,))
    revoker.start()
    # Unfixed, the revocation writes now; fixed, it waits for the paused save.
    revoker.join(timeout=0.5)
    release_first_save.set()
    stale_saver.join(timeout=10)
    revoker.join(timeout=10)
    assert not stale_saver.is_alive() and not revoker.is_alive()

    on_disk = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert revoked not in on_disk
    assert issued["bob"] in on_disk
    restarted = auth_mod.AuthManager(str(auth_path))
    assert not restarted.validate_token(revoked)
    assert restarted.validate_token(issued["bob"])
