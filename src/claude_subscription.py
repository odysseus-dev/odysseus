"""Claude (Anthropic) subscription token provider.

Lets an Odysseus admin use their Claude Pro/Max **subscription** for inference
instead of a pay-per-token Anthropic API key, by pasting a Claude Code OAuth
token (run ``claude setup-token`` locally, or paste the credential JSON Claude
Code stores). Mirrors the existing ChatGPT Subscription provider.

Why paste a token instead of an in-app OAuth flow: the Claude subscription
OAuth (``claude setup-token`` / the Claude Code CLI) redirects to a
``http://localhost:PORT/callback`` loopback, which a browser cannot reach for a
*remote* server. So the user runs the OAuth where the loopback works (their own
machine) and pastes the resulting token here.

Auth differs from the API-key Anthropic provider in exactly one way: requests
carry ``Authorization: Bearer <token>`` plus ``anthropic-beta: oauth-2025-04-20``
instead of ``x-api-key``. Everything else (the /v1/messages payload, response
parsing, streaming) is shared with the Anthropic path in ``src/llm_core.py``.

Note on terms of service: a subscription is intended for first-party Claude
surfaces (Claude apps, Claude Code). Routing a third-party app through it is a
grey area and the account owner accepts that risk by connecting here. The
mechanism is symmetric with the existing ChatGPT Subscription provider.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

CLAUDE_SUBSCRIPTION_PROVIDER = "claude-subscription"

# Real Anthropic API root that requests are sent to.
ANTHROPIC_API_BASE = (
    os.getenv("CLAUDE_SUBSCRIPTION_API_BASE", "").strip().rstrip("/")
    or "https://api.anthropic.com"
)
# Sentinel base_url stored on the ModelEndpoint / ProviderAuthSession. The
# trailing ``/oauth`` marker is what makes ``_detect_provider`` classify this
# endpoint as ``claude-subscription`` (OAuth bearer) rather than the API-key
# ``anthropic`` provider — both live on the same host. It is stripped back to
# ANTHROPIC_API_BASE when the real /v1/messages or /v1/models URL is built.
DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL = f"{ANTHROPIC_API_BASE}/oauth"

# Public OAuth client used by the Claude Code CLI / `claude setup-token`. Only
# used to refresh a token that was pasted together with a refresh token.
CLAUDE_OAUTH_CLIENT_ID = (
    os.getenv("CLAUDE_OAUTH_CLIENT_ID", "").strip()
    or "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
)
CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"

# Beta header that authorizes an OAuth-bearer token on /v1/messages.
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"
ANTHROPIC_VERSION = "2023-06-01"

# Refresh the access token this many seconds before it actually expires.
CLAUDE_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 300
# Tokens pasted without an explicit expiry (e.g. `claude setup-token`, ~1 year)
# get this lifetime so the resolver never tries to refresh a non-refreshable
# token. It is validated against /v1/models at connect time regardless.
CLAUDE_DEFAULT_TOKEN_TTL_DAYS = 365

_AUTH_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_AUTH_REFRESH_LOCKS_GUARD = threading.Lock()


def _database_handles():
    from core.database import ProviderAuthSession, SessionLocal, utcnow_naive

    return ProviderAuthSession, SessionLocal, utcnow_naive


def _refresh_lock_for(auth_id: str) -> threading.Lock:
    with _AUTH_REFRESH_LOCKS_GUARD:
        lock = _AUTH_REFRESH_LOCKS.get(auth_id)
        if lock is None:
            lock = threading.Lock()
            _AUTH_REFRESH_LOCKS[auth_id] = lock
        return lock


class ClaudeSubscriptionError(RuntimeError):
    """Base error for Claude subscription provider failures."""


class ClaudeSubscriptionReauthRequired(ClaudeSubscriptionError):
    """Stored credentials are invalid/expired beyond refresh; reconnect needed."""


class ClaudeSubscriptionRateLimited(ClaudeSubscriptionError):
    """Upstream quota/rate limit; reconnecting will not fix it."""


class ClaudeSubscriptionAuthNotFound(ClaudeSubscriptionError):
    """No matching owner-scoped auth session exists."""


def is_claude_subscription_base(url: str) -> bool:
    """True for the Claude-subscription sentinel base (``…anthropic.com/oauth``).

    Checked before the plain ``anthropic.com`` host match in ``_detect_provider``
    so an OAuth-backed endpoint is not misread as the API-key provider.
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower().rstrip(".")
        path = (parsed.path or "").rstrip("/")
    except Exception:
        return False
    if not (host == "anthropic.com" or host.endswith(".anthropic.com")):
        return False
    return path.endswith("/oauth")


# ── OAuth bearer headers (shared with src/llm_core anthropic path) ──

def claude_oauth_headers(access_token: Optional[str]) -> Dict[str, str]:
    """Headers for an OAuth-bearer Anthropic request (no x-api-key)."""
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": CLAUDE_OAUTH_BETA,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


# ── Pasted-credential parsing ──

def parse_pasted_credentials(text: str) -> Tuple[str, str, Optional[datetime]]:
    """Parse a pasted Claude token into (access_token, refresh_token, expires_at).

    Accepts either a bare access token (``sk-ant-oat01-…`` from
    ``claude setup-token``) or the credential JSON Claude Code stores
    (``{"claudeAiOauth": {"accessToken", "refreshToken", "expiresAt"}}`` or a
    flat ``{"access_token", ...}``). ``expires_at`` is naive UTC, or None when
    the pasted value carries no expiry.
    """
    text = (text or "").strip()
    if not text:
        return "", "", None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception as exc:
            raise ClaudeSubscriptionReauthRequired(
                "Pasted value is not a valid token or credential JSON."
            ) from exc
        obj = data.get("claudeAiOauth") if isinstance(data.get("claudeAiOauth"), dict) else data
        access = (obj.get("accessToken") or obj.get("access_token") or "").strip()
        refresh = (obj.get("refreshToken") or obj.get("refresh_token") or "").strip()
        raw_exp = obj.get("expiresAt") or obj.get("expires_at")
        expires_at = None
        if isinstance(raw_exp, (int, float)) and raw_exp > 0:
            secs = raw_exp / 1000.0 if raw_exp > 1e12 else float(raw_exp)
            try:
                expires_at = datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None)
            except Exception:
                expires_at = None
        return access, refresh, expires_at
    # Bare access token.
    return text, "", None


# ── Model discovery ──

def fetch_available_models(access_token: str, timeout: float = 12.0) -> List[str]:
    """List Claude chat model IDs available to this subscription via /v1/models."""
    if not access_token:
        return []
    try:
        response = httpx.get(
            f"{ANTHROPIC_API_BASE}/v1/models?limit=100",
            headers=claude_oauth_headers(access_token),
            timeout=timeout,
        )
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []
    entries = data.get("data", []) if isinstance(data, dict) else []
    ordered: List[str] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid.startswith("claude"):
            continue
        if mid not in seen:
            ordered.append(mid)
            seen.add(mid)
    return ordered


# ── Token refresh (only when a refresh token was provided) ──

def refresh_oauth_tokens(refresh_token: str, timeout: float = 20.0) -> Dict[str, Any]:
    if not refresh_token:
        raise ClaudeSubscriptionReauthRequired(
            "Claude Subscription has no refresh token. Reconnect with a fresh token."
        )
    response = httpx.post(
        CLAUDE_OAUTH_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_OAUTH_CLIENT_ID,
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=timeout,
        follow_redirects=True,
    )
    if response.status_code == 429:
        raise ClaudeSubscriptionRateLimited("Claude Subscription rate limit hit during refresh.")
    if response.status_code >= 400:
        raise ClaudeSubscriptionReauthRequired(
            f"Claude Subscription token refresh failed (HTTP {response.status_code}). Reconnect."
        )
    try:
        data = response.json()
    except Exception as exc:
        raise ClaudeSubscriptionError("Claude Subscription refresh returned invalid JSON.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ClaudeSubscriptionReauthRequired("Claude Subscription refresh returned no access token.")
    return data


# ── Runtime credential resolution (refresh-aware) ──

def _access_token_is_expiring(expires_at, utcnow_naive, skew_seconds: int) -> bool:
    """True when there's no stored expiry or it's within ``skew_seconds`` of now."""
    if not expires_at:
        return True
    try:
        return expires_at <= (utcnow_naive() + timedelta(seconds=int(skew_seconds)))
    except Exception:
        return True


def resolve_runtime_credentials(
    auth_id: str, owner: Optional[str] = None, *, force_refresh: bool = False
) -> Dict[str, Any]:
    ProviderAuthSession, SessionLocal, utcnow_naive = _database_handles()
    db = SessionLocal()
    try:
        q = db.query(ProviderAuthSession).filter(
            ProviderAuthSession.id == auth_id,
            ProviderAuthSession.provider == CLAUDE_SUBSCRIPTION_PROVIDER,
        )
        if owner:
            q = q.filter(ProviderAuthSession.owner == owner)
        row = q.first()
        if row is None:
            raise ClaudeSubscriptionAuthNotFound(
                "Claude Subscription credentials were not found for this user."
            )

        expiring = force_refresh or _access_token_is_expiring(
            getattr(row, "expires_at", None), utcnow_naive, CLAUDE_ACCESS_TOKEN_REFRESH_SKEW_SECONDS
        )
        # Only a token pasted *with* a refresh token can be refreshed; a bare
        # `claude setup-token` token cannot, so it's used until it expires.
        if expiring and (row.refresh_token or "").strip():
            with _refresh_lock_for(auth_id):
                db.refresh(row)
                expiring = force_refresh or _access_token_is_expiring(
                    getattr(row, "expires_at", None), utcnow_naive,
                    CLAUDE_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
                )
                if expiring and (row.refresh_token or "").strip():
                    refreshed = refresh_oauth_tokens(row.refresh_token)
                    row.access_token = refreshed["access_token"]
                    if refreshed.get("refresh_token"):
                        row.refresh_token = refreshed["refresh_token"]
                    expires_in = refreshed.get("expires_in")
                    if isinstance(expires_in, (int, float)) and expires_in > 0:
                        row.expires_at = utcnow_naive() + timedelta(seconds=int(expires_in))
                    row.last_refresh = utcnow_naive()
                    db.commit()
                    db.refresh(row)

        return {
            "provider": CLAUDE_SUBSCRIPTION_PROVIDER,
            "base_url": (row.base_url or DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL).rstrip("/"),
            "api_key": row.access_token or "",
            "auth_mode": row.auth_mode or "claude",
        }
    finally:
        db.close()


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ClaudeSubscriptionRateLimited):
        return HTTPException(429, str(exc))
    if isinstance(exc, (ClaudeSubscriptionReauthRequired, ClaudeSubscriptionAuthNotFound)):
        return HTTPException(401, f"{exc} Reconnect the provider.")
    return HTTPException(502, str(exc))
