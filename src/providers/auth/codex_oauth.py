"""OpenAI Codex (ChatGPT-subscription) OAuth protocol — device-code login + refresh.

Pure protocol helpers against `auth.openai.com`. This module does network I/O
(its job is credential acquisition) but **no DB and no logging of secrets** —
persistence is the caller's (the connect route + `credentials.resolve`).

Why device-code (not loopback-PKCE like the codex CLI / pi / opencode): Odysseus
runs on a remote host reached from a browser elsewhere, so a `localhost:1455`
callback can never land, and codex's public client whitelists loopback-only
redirects. Device-code needs no redirect — OpenAI's deviceauth service even
generates the PKCE pair server-side and hands back BOTH the `authorization_code`
and the matching `code_verifier` in the poll response; we just forward them to
`/oauth/token`. (Wire format validated against open-source codex clients running
this exact pattern on remote deployments.)

NEVER log `access_token` / `refresh_token` / `code_verifier` / `device_auth_id`
/ `account_id`. Errors carry only status codes and OAuth error *codes*.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Codex CLI's public client id — reused by every Codex OAuth client (pi,
# opencode, codex itself). No codex CLI binary is required.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

ISSUER = "https://auth.openai.com"
TOKEN_URL = f"{ISSUER}/oauth/token"
DEVICE_USERCODE_URL = f"{ISSUER}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{ISSUER}/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URI = f"{ISSUER}/codex/device"
DEVICE_REDIRECT_URI = f"{ISSUER}/deviceauth/callback"

# Inference base URL for codex-backed ChatGPT-subscription endpoints.
BASE_URL = "https://chatgpt.com/backend-api/codex"

# JWT claim namespace that carries `chatgpt_account_id`.
JWT_AUTH_CLAIM = "https://api.openai.com/auth"

# Refresh the access token this many seconds before its JWT `exp`.
REFRESH_SKEW_SECONDS = 120

# Login default poll cadence / TTL when the server omits them.
DEFAULT_POLL_INTERVAL_SECONDS = 5
MIN_POLL_INTERVAL_SECONDS = 3
DEFAULT_DEVICE_CODE_TTL_SECONDS = 15 * 60

_HTTP_TIMEOUT = httpx.Timeout(20.0)


def _new_async_client():
    """Single construction point for the device-code/refresh HTTP client.

    Centralized so the call sites share one config and tests can inject an
    ``httpx.MockTransport`` here — no network-mocking dependency required.
    Return type is inferred (``httpx.AsyncClient``) to stay parity-clean with
    the prior inline construction."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


# OAuth error codes that mean the refresh token is dead → user must reconnect.
_RELOGIN_ERROR_CODES = frozenset(
    {"invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused"}
)


class CodexAuthError(Exception):
    """A Codex OAuth failure with a redacted message and routing hints.

    `relogin_required` distinguishes a dead refresh token (reconnect needed)
    from a transient/quota condition (`code == "quota"`, credentials still
    valid). The message never contains token material.
    """

    def __init__(self, message: str, *, code: str, relogin_required: bool = False):
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required


@dataclass(frozen=True)
class DeviceCodeStart:
    """The user-facing half of a started device-code login (no secrets)."""
    user_code: str
    device_auth_id: str          # poll secret — encrypt at rest, never log/expose
    verification_uri: str
    interval: int
    expires_at: Optional[datetime]   # naive UTC


@dataclass(frozen=True)
class DeviceAuthResult:
    """A successful poll: the codes to redeem at the token endpoint."""
    authorization_code: str
    code_verifier: str           # server-generated PKCE verifier


@dataclass(frozen=True)
class TokenSet:
    """A resolved credential pair plus derived identity/expiry (no logging)."""
    access_token: str
    refresh_token: str
    account_id: Optional[str]
    expires_at: Optional[datetime]   # naive UTC, from the access-token JWT `exp`


# ---------------------------------------------------------------------------
# JWT helpers (no verification — we only read public claims for routing)
# ---------------------------------------------------------------------------

def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_naive_from_epoch(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


def decode_jwt_claims(token: Any) -> Dict[str, Any]:
    """Decode a JWT payload segment to its claims dict (no signature check)."""
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        claims = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def extract_account_id(access_token: Any) -> Optional[str]:
    """Pull `chatgpt_account_id` from the access-token JWT, tolerating absence."""
    claims = decode_jwt_claims(access_token)
    auth = claims.get(JWT_AUTH_CLAIM)
    if isinstance(auth, dict):
        acct = auth.get("chatgpt_account_id")
        if isinstance(acct, str) and acct.strip():
            return acct.strip()
    return None


def access_token_expiry(access_token: Any) -> Optional[datetime]:
    """Naive-UTC expiry from the JWT `exp` claim, or None if unreadable."""
    exp = decode_jwt_claims(access_token).get("exp")
    if isinstance(exp, (int, float)):
        return _utc_naive_from_epoch(float(exp))
    return None


def access_token_is_expiring(access_token: Any, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    """True when the access token expires within `skew_seconds`. Unknown → False
    (let a real 401 trigger refresh rather than churning on every call)."""
    expiry = access_token_expiry(access_token)
    if expiry is None:
        return False
    return expiry <= _utcnow_naive() + _timedelta(skew_seconds)


def _timedelta(seconds: int) -> timedelta:
    return timedelta(seconds=max(0, int(seconds)))


# ---------------------------------------------------------------------------
# Device-code login
# ---------------------------------------------------------------------------

async def request_device_code(client: Optional[httpx.AsyncClient] = None) -> DeviceCodeStart:
    """Step 1: ask the deviceauth service for a user_code + device_auth_id."""
    body = {"client_id": CLIENT_ID}
    headers = {"Content-Type": "application/json"}
    data = await _post_json(client, DEVICE_USERCODE_URL, body, headers, what="device code request")

    user_code = str(data.get("user_code") or "")
    device_auth_id = str(data.get("device_auth_id") or "")
    if not user_code or not device_auth_id:
        raise CodexAuthError("Device code response missing required fields.",
                             code="device_code_incomplete")

    interval = max(MIN_POLL_INTERVAL_SECONDS,
                   _as_int(data.get("interval"), DEFAULT_POLL_INTERVAL_SECONDS))
    ttl = _as_int(data.get("expires_in"), DEFAULT_DEVICE_CODE_TTL_SECONDS)
    expires_at = _utcnow_naive() + _timedelta(ttl)

    logger.info("codex device-code: issued (interval=%ss, ttl=%ss)", interval, ttl)
    return DeviceCodeStart(
        user_code=user_code,
        device_auth_id=device_auth_id,
        verification_uri=DEVICE_VERIFICATION_URI,
        interval=interval,
        expires_at=expires_at,
    )


async def poll_device_token(
    device_auth_id: str, user_code: str, client: Optional[httpx.AsyncClient] = None
) -> Optional[DeviceAuthResult]:
    """Step 3 (one attempt): poll for authorization. Returns None while the user
    has not finished (403/404); raises CodexAuthError on a hard failure."""
    body = {"device_auth_id": device_auth_id, "user_code": user_code}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    owns = client is None
    client = client or _new_async_client()
    try:
        resp = await client.post(DEVICE_TOKEN_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise CodexAuthError(f"Device auth poll failed: {type(exc).__name__}",
                             code="device_code_poll_failed") from exc
    finally:
        if owns:
            await client.aclose()

    if resp.status_code == 200:
        payload = _json_or_error(resp, "device auth poll")
        authorization_code = str(payload.get("authorization_code") or "")
        code_verifier = str(payload.get("code_verifier") or "")
        if not authorization_code or not code_verifier:
            raise CodexAuthError(
                "Device auth response missing authorization_code or code_verifier.",
                code="device_code_incomplete_exchange",
            )
        return DeviceAuthResult(authorization_code=authorization_code, code_verifier=code_verifier)

    if resp.status_code in (403, 404):
        return None  # user hasn't completed sign-in yet — keep polling

    raise CodexAuthError(f"Device auth polling returned status {resp.status_code}.",
                         code="device_code_poll_error")


async def exchange_device_code(
    authorization_code: str, code_verifier: str, client: Optional[httpx.AsyncClient] = None
) -> TokenSet:
    """Step 4: redeem the authorization code (+ server PKCE verifier) for tokens."""
    form = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": DEVICE_REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    }
    payload = await _post_form(client, form, what="token exchange")
    tokens = _parse_token_payload(payload, prior_refresh=None, require_refresh=True)
    logger.info("codex device-code: token exchange succeeded")
    return tokens


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

async def refresh_tokens(
    refresh_token: str, client: Optional[httpx.AsyncClient] = None
) -> TokenSet:
    """Refresh the access token. Rotates the refresh token only when the server
    returns a new one (otherwise the prior one is kept). Classifies 429 (quota,
    still valid) distinctly from auth failures (relogin required)."""
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError("Missing refresh_token.", code="missing_refresh_token",
                             relogin_required=True)

    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token.strip(),
        "client_id": CLIENT_ID,
    }
    payload = await _post_form(client, form, what="token refresh")
    tokens = _parse_token_payload(payload, prior_refresh=refresh_token.strip(), require_refresh=False)
    logger.info("codex: token refresh succeeded")
    return tokens


# ---------------------------------------------------------------------------
# Shared HTTP + parsing
# ---------------------------------------------------------------------------

async def _post_json(
    client: Optional[httpx.AsyncClient], url: str, body: dict, headers: dict, *, what: str
) -> dict:
    owns = client is None
    client = client or _new_async_client()
    try:
        resp = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise CodexAuthError(f"{what} failed: {type(exc).__name__}",
                             code="network_error") from exc
    finally:
        if owns:
            await client.aclose()
    if resp.status_code != 200:
        raise _classify_token_error(resp, what)
    return _json_or_error(resp, what)


async def _post_form(
    client: Optional[httpx.AsyncClient], form: dict, *, what: str
) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    owns = client is None
    client = client or _new_async_client()
    try:
        resp = await client.post(TOKEN_URL, data=form, headers=headers)
    except httpx.HTTPError as exc:
        raise CodexAuthError(f"{what} failed: {type(exc).__name__}",
                             code="network_error") from exc
    finally:
        if owns:
            await client.aclose()
    if resp.status_code != 200:
        raise _classify_token_error(resp, what)
    return _json_or_error(resp, what)


def _json_or_error(resp: httpx.Response, what: str) -> dict:
    try:
        data = resp.json()
    except Exception as exc:
        raise CodexAuthError(f"{what} returned invalid JSON.", code="invalid_json") from exc
    if not isinstance(data, dict):
        raise CodexAuthError(f"{what} returned an unexpected payload.", code="invalid_payload")
    return data


def _parse_token_payload(payload: dict, *, prior_refresh: Optional[str], require_refresh: bool) -> TokenSet:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise CodexAuthError("Token response missing access_token.",
                             code="missing_access_token", relogin_required=True)
    access_token = access_token.strip()

    next_refresh = payload.get("refresh_token")
    if isinstance(next_refresh, str) and next_refresh.strip():
        refresh_token = next_refresh.strip()
    elif prior_refresh:
        refresh_token = prior_refresh
    elif require_refresh:
        raise CodexAuthError("Token response missing refresh_token.",
                             code="missing_refresh_token", relogin_required=True)
    else:
        refresh_token = ""

    # Prefer the JWT `exp`; fall back to expires_in if the token is opaque.
    expires_at = access_token_expiry(access_token)
    if expires_at is None:
        ttl = _as_int(payload.get("expires_in"), 0)
        if ttl > 0:
            expires_at = _utcnow_naive() + _timedelta(ttl)

    return TokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=extract_account_id(access_token),
        expires_at=expires_at,
    )


def _classify_token_error(resp: httpx.Response, what: str) -> CodexAuthError:
    """Map a non-200 token-endpoint response to a redacted, routable error."""
    status = resp.status_code
    if status == 429:
        return CodexAuthError(
            "Codex provider quota exhausted (429). Credentials are still valid; "
            "retry after the usage limit resets.",
            code="quota", relogin_required=False,
        )

    code = "codex_token_error"
    relogin = False
    try:
        err = resp.json()
    except Exception:
        err = None
    if isinstance(err, dict):
        err_obj = err.get("error")
        if isinstance(err_obj, dict):
            nested = err_obj.get("code") or err_obj.get("type")
            if isinstance(nested, str) and nested.strip():
                code = nested.strip()
        elif isinstance(err_obj, str) and err_obj.strip():
            code = err_obj.strip()
    if code in _RELOGIN_ERROR_CODES:
        relogin = True
    if status in (401, 403):
        relogin = True

    return CodexAuthError(f"{what} failed (status {status}, code {code}).",
                          code=code, relogin_required=relogin)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
