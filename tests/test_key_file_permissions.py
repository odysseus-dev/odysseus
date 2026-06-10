"""Regression: APIKeyManager must write .key with 0o600 on POSIX.

On Windows the test is skipped (safe_chmod is a documented no-op).
"""
import os
import stat
import sys
import pytest
from src.api_key_manager import APIKeyManager

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")

def test_key_file_created_with_0600(tmp_path):
    mgr = APIKeyManager(str(tmp_path))
    # Trigger key creation
    mgr.get_or_create_key()
    key_file = tmp_path / ".key"
    assert key_file.exists(), ".key file must be created"
    mode = stat.S_IMODE(os.stat(str(key_file)).st_mode)
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"

def test_existing_key_file_hardened_on_load(tmp_path):
    # Create a .key file with permissive mode first (simulates legacy)
    mgr = APIKeyManager(str(tmp_path))
    mgr.get_or_create_key()  # Create it
    key_file = tmp_path / ".key"
    os.chmod(str(key_file), 0o644)  # Widen it
    # Now reload — get_or_create_key should re-apply 0o600
    mgr.get_or_create_key()
    mode = stat.S_IMODE(os.stat(str(key_file)).st_mode)
    assert mode == 0o600, f"Expected 0o600 after harden, got 0o{mode:o}"
