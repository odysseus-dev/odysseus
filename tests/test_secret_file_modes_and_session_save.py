"""Secret-bearing files are owner-only from creation.

* Files holding secrets were created world-readable. ``atomic_write_json``
  opened its temp file with plain ``open()`` (0o666 & ~umask, so 0o644 under the
  usual 022) and that is the file ``sessions.json`` (raw session tokens) and
  ``auth.json`` (TOTP secrets, backup codes) became. The vault config holding
  the Bitwarden session key was written with ``write_text`` and only then
  chmod'ed, and ``data/.app_key`` was generated with no exclusive create, so two
  first-start callers could each write their own key.

The mode tests disable ``os.chmod``: a file that is only tightened after it
was written still exposes the secret in between, so they check the mode the
file was created with. Everything they write stays inside ``tmp_path``; none of
them runs git.
"""

import asyncio
import json
import os
import stat
import threading

import pytest
from cryptography.fernet import Fernet

from core import atomic_io
from routes.vault import vault_routes
from src import secret_storage, tool_approval_scopes
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
    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / "data" / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    return secret_storage


@posix_only
def test_concurrent_first_key_loads_share_one_owner_only_key(key_storage, monkeypatch, umask_022, no_chmod):
    ss = key_storage
    both_generated = threading.Barrier(2, timeout=0.5)

    class _SlowFernet(Fernet):
        @staticmethod
        def generate_key():
            key = Fernet.generate_key()
            # Hold both first-start callers between "no key yet" and the
            # write. Callers that are serialized never meet here: the first
            # one times out and carries on alone.
            try:
                both_generated.wait()
            except threading.BrokenBarrierError:
                pass
            return key

    monkeypatch.setattr(ss, "Fernet", _SlowFernet)

    results = {}

    def encrypt_first():
        results["token"] = ss.encrypt("imap-password")

    def grant_key_first():
        # The direct _load_or_create_key path tool approval signatures use.
        results["grant_key"] = tool_approval_scopes._grant_key()

    threads = [threading.Thread(target=encrypt_first), threading.Thread(target=grant_key_first)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)

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
