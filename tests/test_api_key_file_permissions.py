"""Regression: the API-key encryption key file (data/.key) must be owner-only
(0o600).

``APIKeyManager.get_or_create_key`` writes the Fernet key that decrypts *every*
stored provider credential. Older versions created it with the process umask
(commonly 0o644 — group/world-readable). It must be locked to the owner, both
when freshly created and when an older, too-permissive key is read back.

POSIX-only: ``core.platform_compat.safe_chmod`` is a documented no-op on Windows
(files under the user profile are ACL-restricted), so the mode assertions are
skipped there.
"""
import os
import stat
import sys

import pytest

from src.api_key_manager import APIKeyManager

_WINDOWS = sys.platform.startswith("win")


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.skipif(_WINDOWS, reason="POSIX permission bits only")
def test_new_key_file_is_owner_only(tmp_path):
    mgr = APIKeyManager(str(tmp_path))
    mgr.get_or_create_key()
    assert _mode(mgr.key_file) == 0o600, f"expected 0o600, got {oct(_mode(mgr.key_file))}"


@pytest.mark.skipif(_WINDOWS, reason="POSIX permission bits only")
def test_existing_world_readable_key_is_relocked(tmp_path):
    mgr = APIKeyManager(str(tmp_path))
    # Simulate a key written by an older version with a permissive umask.
    with open(mgr.key_file, "wb") as f:
        f.write(b"x" * 44)
    os.chmod(mgr.key_file, 0o644)
    mgr.get_or_create_key()  # existing-file branch should re-lock it
    assert _mode(mgr.key_file) == 0o600, f"expected re-lock to 0o600, got {oct(_mode(mgr.key_file))}"


def test_encrypt_decrypt_roundtrip_still_works(tmp_path):
    # The permission hardening must not change functional behaviour.
    mgr = APIKeyManager(str(tmp_path))
    enc = mgr.encrypt_api_key("sk-secret")
    assert enc and enc != "sk-secret"
    assert mgr.decrypt_api_key(enc) == "sk-secret"
