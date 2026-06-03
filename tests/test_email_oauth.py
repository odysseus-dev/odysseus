"""Unit tests for src/email_oauth.py — pure, no network (the one egress point,
``_post_form``, is monkeypatched)."""

import base64
import time

import pytest

from src import email_oauth as eo


def test_provider_preset_known_and_unknown():
    assert eo.provider_preset("gmail")["imap_host"] == "imap.gmail.com"
    assert eo.provider_preset("OUTLOOK")["smtp_host"] == "smtp.office365.com"
    with pytest.raises(eo.EmailOAuthError):
        eo.provider_preset("yahoo-nope")


def test_list_providers_shape_has_no_secrets():
    provs = eo.list_providers()
    ids = {p["id"] for p in provs}
    assert {"gmail", "outlook"} <= ids
    for p in provs:
        assert set(p) == {"id", "label", "imap_host", "imap_port", "smtp_host", "smtp_port"}


def test_build_authorize_url_gmail_offline_consent():
    url = eo.build_authorize_url(
        "gmail", "cid.apps.googleusercontent.com",
        "http://localhost:7000/api/email/oauth/callback", "state123",
        login_hint="me@gmail.com",
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "response_type=code" in url
    assert "client_id=cid.apps.googleusercontent.com" in url
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "state=state123" in url
    assert "login_hint=me%40gmail.com" in url
    assert "mail.google.com" in url  # scope present (url-encoded)


def test_build_authorize_url_requires_state():
    with pytest.raises(AssertionError):
        eo.build_authorize_url("gmail", "cid", "http://x/cb", "")


def test_xoauth2_sasl_is_raw_for_stdlib_auth():
    # imaplib/smtplib base64-encode themselves, so we must hand them the raw form.
    raw = eo.xoauth2_sasl("me@example.com", "ACCESS123")
    assert raw == "user=me@example.com\x01auth=Bearer ACCESS123\x01\x01"


def test_xoauth2_token_decodes_to_sasl_string():
    tok = eo.xoauth2_token("me@example.com", "ACCESS123")
    decoded = base64.b64decode(tok).decode("utf-8")
    assert decoded == "user=me@example.com\x01auth=Bearer ACCESS123\x01\x01"


def test_is_expired_bounds():
    assert eo.is_expired(None) is True
    assert eo.is_expired(0) is True
    assert eo.is_expired(time.time() + 3600) is False
    # Within the skew window → treated as expired (refresh early).
    assert eo.is_expired(time.time() + 60, skew=120) is True


def test_exchange_code_maps_tokens(monkeypatch):
    captured = {}

    def fake_post(url, data):
        captured["url"] = url
        captured["data"] = data
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                "token_type": "Bearer"}

    monkeypatch.setattr(eo, "_post_form", fake_post)
    out = eo.exchange_code("gmail", "cid", "secret", "the-code", "http://x/cb")
    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "the-code"
    assert out["access_token"] == "AT" and out["refresh_token"] == "RT"
    assert abs(out["expires_at"] - (time.time() + 3600)) < 5


def test_refresh_keeps_existing_refresh_token(monkeypatch):
    # Providers commonly omit refresh_token on refresh — we must keep the old one.
    monkeypatch.setattr(eo, "_post_form",
                        lambda url, data: {"access_token": "AT2", "expires_in": 1800})
    out = eo.refresh_access_token("outlook", "cid", "secret", "ORIGINAL_RT")
    assert out["access_token"] == "AT2"
    assert out["refresh_token"] == "ORIGINAL_RT"
    assert out["expires_at"] > time.time()


def test_token_error_response_raises(monkeypatch):
    monkeypatch.setattr(eo, "_post_form",
                        lambda url, data: {"error": "invalid_grant",
                                           "error_description": "bad code"})
    with pytest.raises(eo.EmailOAuthError) as ei:
        eo.exchange_code("gmail", "cid", "secret", "bad", "http://x/cb")
    assert "bad code" in str(ei.value)
