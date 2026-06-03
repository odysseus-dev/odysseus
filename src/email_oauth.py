"""
email_oauth.py — OAuth2 (XOAUTH2) support for IMAP / SMTP email accounts.

Self-hosted-friendly design: the operator brings their own OAuth client
(``client_id`` + ``client_secret`` registered in their Google Cloud or
Microsoft Entra console). No shared app credentials ship with the project, so
there is nothing central to leak or rate-limit. This module implements the
OAuth2 authorization-code flow, refresh-token rotation, and the SASL XOAUTH2
string that authenticates IMAP and SMTP in place of a password.

Pure module: no database, no web framework, no third-party imports — only the
standard library — so it is trivial to unit-test and carries no new
dependency. All network egress funnels through ``_post_form`` (the single
choke point tests monkeypatch).
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

# Clock skew (seconds) subtracted when judging token expiry, so we refresh a
# little early rather than racing the server's own expiry check.
_EXPIRY_SKEW_SECONDS = 120
# Hard cap on token-endpoint round trips. Bounded per NASA Power-of-Ten rule 2.
_HTTP_TIMEOUT_SECONDS = 30


class EmailOAuthError(Exception):
    """Raised when an OAuth2 token operation fails (network, HTTP, or parse)."""


# Provider presets. ``scopes`` request full IMAP+SMTP mailbox access; the
# operator's registered client governs what is actually granted. Hosts/ports
# are the published defaults for each provider and may be overridden per
# account.
PROVIDERS: dict[str, dict] = {
    "gmail": {
        "label": "Google (Gmail)",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://mail.google.com/"],
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        # Google only returns a refresh_token when these are present.
        "extra_auth_params": {"access_type": "offline", "prompt": "consent"},
    },
    "outlook": {
        "label": "Microsoft (Outlook / Office 365)",
        "auth_uri": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_uri": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "https://outlook.office.com/IMAP.AccessAsUser.All",
            "https://outlook.office.com/SMTP.Send",
            "offline_access",
        ],
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "extra_auth_params": {},
    },
}


def provider_preset(provider: str) -> dict:
    """Return the preset for ``provider`` (case-insensitive). Raises on unknown."""
    assert isinstance(provider, str) and provider, "provider must be a non-empty string"
    preset = PROVIDERS.get(provider.strip().lower())
    if preset is None:
        raise EmailOAuthError(f"Unknown OAuth provider: {provider!r}")
    return preset


def list_providers() -> list[dict]:
    """Public provider catalogue for the settings UI (no secrets)."""
    return [
        {
            "id": pid,
            "label": p["label"],
            "imap_host": p["imap_host"],
            "imap_port": p["imap_port"],
            "smtp_host": p["smtp_host"],
            "smtp_port": p["smtp_port"],
        }
        for pid, p in PROVIDERS.items()
    ]


def build_authorize_url(
    provider: str, client_id: str, redirect_uri: str, state: str,
    login_hint: str | None = None,
) -> str:
    """Build the provider authorization-code URL the user is sent to."""
    preset = provider_preset(provider)
    assert client_id, "client_id is required"
    assert redirect_uri, "redirect_uri is required"
    assert state, "state is required (CSRF protection)"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(preset["scopes"]),
        "state": state,
    }
    params.update(preset.get("extra_auth_params", {}))
    if login_hint:
        params["login_hint"] = login_hint
    return preset["auth_uri"] + "?" + urllib.parse.urlencode(params)


def _post_form(url: str, data: dict) -> dict:
    """POST application/x-www-form-urlencoded, return parsed JSON. Sole egress."""
    assert url, "url is required"
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise EmailOAuthError(f"token endpoint HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise EmailOAuthError(f"token endpoint unreachable: {exc.reason}") from exc
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EmailOAuthError(f"token endpoint returned non-JSON: {payload[:200]}") from exc
    if not isinstance(parsed, dict):
        raise EmailOAuthError("token endpoint returned a non-object JSON body")
    return parsed


def _tokens_from_response(resp: dict, fallback_refresh: str = "") -> dict:
    """Normalise a token-endpoint response into our stored shape. Checked."""
    access = resp.get("access_token")
    if not access:
        err = resp.get("error_description") or resp.get("error") or "no access_token"
        raise EmailOAuthError(f"token exchange failed: {err}")
    expires_in = int(resp.get("expires_in") or 3600)
    return {
        "access_token": access,
        # Providers omit refresh_token on refresh; keep the one we already hold.
        "refresh_token": resp.get("refresh_token") or fallback_refresh,
        "expires_at": int(time.time()) + max(0, expires_in),
        "token_type": resp.get("token_type") or "Bearer",
    }


def exchange_code(
    provider: str, client_id: str, client_secret: str, code: str, redirect_uri: str,
) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    preset = provider_preset(provider)
    assert code and client_id and redirect_uri, "code, client_id, redirect_uri required"
    resp = _post_form(preset["token_uri"], {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret or "",
        "redirect_uri": redirect_uri,
    })
    return _tokens_from_response(resp)


def refresh_access_token(
    provider: str, client_id: str, client_secret: str, refresh_token: str,
) -> dict:
    """Use a refresh token to mint a fresh access token."""
    preset = provider_preset(provider)
    assert refresh_token and client_id, "refresh_token and client_id required"
    resp = _post_form(preset["token_uri"], {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret or "",
    })
    return _tokens_from_response(resp, fallback_refresh=refresh_token)


def is_expired(expires_at: int | float | None, skew: int = _EXPIRY_SKEW_SECONDS) -> bool:
    """True if a token at ``expires_at`` (epoch seconds) is at/near expiry."""
    if not expires_at:
        return True
    return time.time() >= (float(expires_at) - skew)


def xoauth2_sasl(user: str, access_token: str) -> str:
    """Build the *raw* (un-encoded) SASL XOAUTH2 client response.

    Use this with ``imaplib.IMAP4.authenticate`` and ``smtplib.SMTP.auth`` —
    both base64-encode the value themselves, so handing them base64 would
    double-encode and fail the handshake.
    """
    assert user, "user is required"
    assert access_token, "access_token is required"
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def xoauth2_token(user: str, access_token: str) -> str:
    """Base64 SASL XOAUTH2 initial-client-response, for transports (e.g. a raw
    ``AUTH XOAUTH2 <token>`` ``docmd``) that expect a pre-encoded value."""
    return base64.b64encode(xoauth2_sasl(user, access_token).encode("utf-8")).decode("ascii")
