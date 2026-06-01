"""Tests for the Google OAuth2 email helpers.

Covers the security-critical surface added for Google Workspace / .edu
IMAP/SMTP support:

- `make_oauth_state` / `verify_oauth_state` — HMAC-signed OAuth state so the
  callback can't be CSRF'd or have its account_id/owner tampered with.
- `_smtp_ready` — an OAuth account (no stored password) must still count as
  send-capable; a host+user-only account without password or OAuth must not.
- `_xoauth2_string` / `_xoauth2_bytes` — SASL XOAUTH2 framing for SMTP/IMAP.

These are pure-function tests — no FastAPI app boot, no live network.
"""

import base64
import json

import pytest


# ── OAuth state signing ──────────────────────────────────────────

def test_oauth_state_round_trips_account_and_owner():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    state = make_oauth_state("acct-123", "user@example.com")
    payload = verify_oauth_state(state)

    assert payload is not None
    assert payload["a"] == "acct-123"
    assert payload["o"] == "user@example.com"
    assert payload["n"]  # nonce present


def test_oauth_state_nonce_is_unique_per_call():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    a = verify_oauth_state(make_oauth_state("acct", "o"))
    b = verify_oauth_state(make_oauth_state("acct", "o"))
    assert a["n"] != b["n"]


def test_oauth_state_rejects_tampered_account_id():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    state = make_oauth_state("acct-123", "user@example.com")
    decoded = base64.urlsafe_b64decode(state.encode()).decode()
    payload_str, sig = decoded.rsplit("|", 1)
    payload = json.loads(payload_str)
    payload["a"] = "evil-acct"  # attacker swaps the target account
    forged = base64.urlsafe_b64encode(
        (json.dumps(payload, separators=(",", ":")) + "|" + sig).encode()
    ).decode()

    assert verify_oauth_state(forged) is None


def test_oauth_state_rejects_forged_signature():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    state = make_oauth_state("acct-123", "user@example.com")
    decoded = base64.urlsafe_b64decode(state.encode()).decode()
    payload_str, _ = decoded.rsplit("|", 1)
    forged = base64.urlsafe_b64encode((payload_str + "|" + "deadbeef" * 8).encode()).decode()

    assert verify_oauth_state(forged) is None


@pytest.mark.parametrize("garbage", ["", "not-base64-at-all", "###", "a|b|c"])
def test_oauth_state_rejects_garbage(garbage):
    from routes.email_helpers import verify_oauth_state

    assert verify_oauth_state(garbage) is None


# ── _smtp_ready: OAuth accounts have no password but can still send ──

def test_smtp_ready_true_for_oauth_account_without_password():
    from routes.email_routes import _smtp_ready

    cfg = {
        "smtp_host": "smtp.gmail.com",
        "smtp_user": "me@nyu.edu",
        "smtp_password": "",
        "oauth_provider": "google",
    }
    assert _smtp_ready(cfg) is True


def test_smtp_ready_true_for_password_account():
    from routes.email_routes import _smtp_ready

    cfg = {
        "smtp_host": "smtp.example.com",
        "smtp_user": "me@example.com",
        "smtp_password": "app-password",
        "oauth_provider": "",
    }
    assert _smtp_ready(cfg) is True


def test_smtp_ready_false_without_password_or_oauth():
    from routes.email_routes import _smtp_ready

    cfg = {
        "smtp_host": "smtp.example.com",
        "smtp_user": "me@example.com",
        "smtp_password": "",
        "oauth_provider": "",
    }
    assert _smtp_ready(cfg) is False


def test_smtp_ready_false_without_host():
    from routes.email_routes import _smtp_ready

    cfg = {"smtp_host": "", "smtp_user": "me@x.com", "oauth_provider": "google"}
    assert _smtp_ready(cfg) is False


# ── XOAUTH2 SASL framing ─────────────────────────────────────────

def test_xoauth2_string_is_base64_of_sasl_frame():
    from routes.email_helpers import _xoauth2_string

    encoded = _xoauth2_string("me@nyu.edu", "tok123")
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "user=me@nyu.edu\x01auth=Bearer tok123\x01\x01"


def test_xoauth2_bytes_is_raw_unencoded_frame():
    from routes.email_helpers import _xoauth2_bytes

    raw = _xoauth2_bytes("me@nyu.edu", "tok123")
    assert raw == b"user=me@nyu.edu\x01auth=Bearer tok123\x01\x01"
