"""Secrets-at-rest hardening.

Covers two behaviours added in this slice:
  1. secret_storage._load_or_create_key honours an APP_KEY env var (12-factor
     master-key injection) and only falls back to the key file when APP_KEY is
     absent or malformed.
  2. APIKeyManager.save writes api_keys.json atomically and, on POSIX, 0o600 —
     so a crash mid-write can't truncate it and the encrypted store is
     owner-only.
"""
import base64
import json
import os
import stat

import pytest
from cryptography.fernet import Fernet

from src import secret_storage
from src.api_key_manager import APIKeyManager

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")


def _reset_fernet(monkeypatch, key_path):
    monkeypatch.setattr(secret_storage, "_KEY_PATH", key_path)
    monkeypatch.setattr(secret_storage, "_fernet", None)


def test_app_key_env_overrides_key_file(monkeypatch, tmp_path):
    key = Fernet.generate_key()  # 32 random bytes, urlsafe-b64 encoded
    key_path = tmp_path / ".app_key"
    _reset_fernet(monkeypatch, key_path)
    monkeypatch.setenv("APP_KEY", key.decode())

    # The env key is used verbatim and no key file is written to disk.
    assert secret_storage._load_or_create_key() == key
    assert not key_path.exists()

    # Round-trip proves the env key actually drives the cipher: a token produced
    # by secret_storage must decrypt under a fresh Fernet built from the env key.
    token = secret_storage.encrypt("hunter2")
    assert token.startswith("enc:")
    assert Fernet(key).decrypt(token[len("enc:"):].encode()) == b"hunter2"
    assert secret_storage.decrypt(token) == "hunter2"


def test_invalid_app_key_falls_back_to_key_file(monkeypatch, tmp_path):
    key_path = tmp_path / ".app_key"
    _reset_fernet(monkeypatch, key_path)
    monkeypatch.setenv("APP_KEY", "!!! not valid base64 !!!")

    generated = secret_storage._load_or_create_key()
    assert key_path.exists(), "must fall back to creating the key file"
    assert key_path.read_bytes() == generated


def test_wrong_length_app_key_falls_back(monkeypatch, tmp_path):
    key_path = tmp_path / ".app_key"
    _reset_fernet(monkeypatch, key_path)
    # Valid base64 but only 16 bytes — not a 32-byte Fernet key.
    monkeypatch.setenv("APP_KEY", base64.urlsafe_b64encode(b"\x00" * 16).decode())

    secret_storage._load_or_create_key()
    assert key_path.exists(), "a non-32-byte APP_KEY must fall back to the key file"


def test_api_keys_saved_atomically_and_round_trips(tmp_path):
    mgr = APIKeyManager(str(tmp_path))
    mgr.save("openai", "sk-secret-123")

    api_keys_file = tmp_path / "api_keys.json"
    assert api_keys_file.exists()

    # Stored value is encrypted at rest, not plaintext.
    raw = json.loads(api_keys_file.read_text(encoding="utf-8"))
    assert "openai" in raw
    assert raw["openai"] != "sk-secret-123"

    # No leftover temp file from the atomic write.
    assert not list(tmp_path.glob("*.tmp*"))

    # A fresh manager decrypts the same value back.
    assert APIKeyManager(str(tmp_path)).load() == {"openai": "sk-secret-123"}


@POSIX_ONLY
def test_api_keys_file_is_owner_only(tmp_path):
    mgr = APIKeyManager(str(tmp_path))
    mgr.save("openai", "sk-secret-123")
    mode = stat.S_IMODE(os.stat(tmp_path / "api_keys.json").st_mode)
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"
