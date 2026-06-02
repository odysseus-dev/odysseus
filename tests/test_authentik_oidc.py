import pytest

pytest.importorskip("authlib")

from core.auth import AuthManager
from core.oidc import create_state_payload, decode_state_payload, derive_username, sanitize_username


def test_state_payload_roundtrip():
    payload = create_state_payload("/notes")
    decoded = decode_state_payload(payload["payload"])
    assert decoded["state"] == payload["state"]
    assert decoded["nonce"] == payload["nonce"]
    assert decoded["next"] == "/notes"


def test_username_derivation_prefers_preferred_username_then_email_then_sub():
    assert sanitize_username(" Jane.Doe+SSO@Example.com ") == "jane.doe-sso-example.com"
    assert derive_username({"preferred_username": "Jane Doe"}) == "jane-doe"
    assert derive_username({"email": "jane@example.com"}) == "jane"
    assert derive_username({"sub": "abc123XYZ"}) == "authentik-abc123xyz"


def test_passwordless_authentik_user_can_set_password(tmp_path):
    auth_path = tmp_path / "auth.json"
    manager = AuthManager(auth_path=str(auth_path))
    assert manager.create_user("sso-user", None, is_admin=False, passwordless=True)
    assert manager.users["sso-user"]["passwordless"] is True
    assert manager.verify_password("sso-user", "anything") is False
    assert manager.change_password("sso-user", "", "new-password-123") is True
    assert manager.users["sso-user"]["passwordless"] is False
    assert manager.verify_password("sso-user", "new-password-123") is True