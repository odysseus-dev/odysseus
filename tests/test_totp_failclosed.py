"""Regression: 2FA must fail closed when enabled but the secret is missing."""
import json

from core.auth import AuthManager


def test_totp_fails_closed_when_enabled_but_secret_missing(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"users": {
        "alice": {"password_hash": "x", "totp_enabled": True},  # no totp_secret
    }}))
    mgr = AuthManager(str(auth_path))
    # Previously returned True, bypassing the second factor entirely.
    assert mgr.totp_verify("alice", "123456") is False


def test_totp_passes_when_2fa_disabled(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"users": {"bob": {"password_hash": "x"}}}))
    mgr = AuthManager(str(auth_path))
    assert mgr.totp_verify("bob", "000000") is True
