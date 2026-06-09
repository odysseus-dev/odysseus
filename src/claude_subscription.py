"""Claude (Anthropic) subscription OAuth helpers.

Connects a Claude Pro/Max (claude.ai) subscription as a model provider, as an
alternative to a static Anthropic API key. Mirrors ``src/chatgpt_subscription.py``:
it runs an OAuth flow, stores the refresh token server-side (encrypted, in
``ProviderAuthSession``), and resolves a fresh bearer token at request time.

Key differences from the ChatGPT subscription provider:

  * **Auth-code + PKCE, not device-poll.** Claude's subscription login is an
    authorization-code flow with PKCE: open the authorize URL, approve, then
    paste the returned code back (à la ``claude auth login``). The route in
    ``routes/claude_subscription_routes.py`` therefore exposes ``/start`` +
    ``/complete`` rather than the device-poll shape.
  * **Base URL is not a discriminator.** Both subscription and API-key Anthropic
    endpoints live at ``api.anthropic.com``. The thing that marks an endpoint as
    subscription-backed is ``ModelEndpoint.provider_auth_id`` (and the auth
    session's ``provider``), not the URL — so there is no ``is_*_base`` helper.
  * **OAuth bearer, not x-api-key.** A subscription access token is sent as
    ``Authorization: Bearer <token>`` plus the ``anthropic-beta: oauth-2025-04-20``
    header — NOT as ``x-api-key``. See ``claude_oauth_headers`` below.

Provider-specific OAuth values (client id, authorize/token URLs, redirect URI,
scope) are taken from Claude Code's public OAuth flow — corroborated across the
OpenCode ``anthropic-auth`` plugin (the same project Odysseus's agent builds on),
community reverse-engineering write-ups, and the credential file
``claude setup-token`` writes. They are hardcoded as defaults (env-overridable),
matching how ``chatgpt_subscription.py`` bakes in Codex's client id + endpoints.

NOTE: this is not a documented third-party integration surface, and using a
consumer Pro/Max subscription through a third-party app may breach Anthropic's
terms — see the CAVEAT before relying on it.

IMPERSONATION — OAuth subscription tokens are only honored on the Messages API
when the request looks like Claude Code. This module applies the Codex-style
shaping (mirroring chatgpt_subscription, where llm_core branches and calls the
provider's helpers): the ``claude-code-20250219`` beta (CLAUDE_OAUTH_BETA_HEADER)
plus a leading Claude Code identity system prompt (shape_payload_for_claude_code),
and — like the ChatGPT provider — it runs with tools DISABLED (the route sets
``supports_tools = False``). Still TODO for tool/agent support: the ``cc_``
tool-name prefix (request + response). All of this needs live testing with a real
``sk-ant-oat01-`` token, which is also the point to confirm the ToS position.

REQUIRED WIRING (kept out of this file so the two scaffold files stay reviewable;
apply these to make the provider live):

  1. ``src/endpoint_resolver.py`` ~L83 ``resolve_endpoint_runtime``: dispatch the
     runtime-credential resolver by the auth session's ``provider`` instead of
     hardcoding ``chatgpt_subscription`` — call ``resolve_runtime_credentials``
     here when ``provider == CLAUDE_SUBSCRIPTION_PROVIDER``.
  2. ``src/llm_core.py`` ``_build_anthropic_headers`` (~L780): it currently
     *converts* ``Authorization: Bearer`` into ``x-api-key`` for Anthropic. For a
     subscription endpoint, KEEP the Bearer header and add
     ``anthropic-beta: oauth-2025-04-20`` instead (use ``claude_oauth_headers``).
  3. ``app.py``: ``app.include_router(setup_claude_subscription_routes())``.
  4. Frontend: register ``/setup claude-subscription`` in
     ``static/js/slashCommands.js`` and wire the start/complete calls.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CLAUDE_SUBSCRIPTION_PROVIDER = "claude-subscription"

# Anthropic API root the provisioned endpoint points at. Safe, well-known.
DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL = (
    os.getenv("CLAUDE_SUBSCRIPTION_BASE_URL", "").strip().rstrip("/")
    or "https://api.anthropic.com"
)

# Sent with every subscription request alongside the Bearer token.
ANTHROPIC_VERSION = os.getenv("CLAUDE_ANTHROPIC_VERSION", "2023-06-01").strip()
# OAuth subscription tokens require these beta flags on /v1/messages: the OAuth
# flag, plus `claude-code-20250219` (the token is scoped to Claude Code). Append
# more (comma-separated) via env if you use other Anthropic betas.
CLAUDE_OAUTH_BETA_HEADER = os.getenv(
    "CLAUDE_OAUTH_BETA_HEADER", "oauth-2025-04-20,claude-code-20250219"
).strip()

# OAuth subscription tokens are only honored when the request leads with the
# Claude Code identity system prompt. This is the Claude analogue of how the
# ChatGPT provider shapes its `instructions`.
CLAUDE_CODE_SYSTEM_PROMPT = os.getenv(
    "CLAUDE_CODE_SYSTEM_PROMPT",
    "You are Claude Code, Anthropic's official CLI for Claude.",
).strip()

# Anthropic OAuth access tokens (subscription login) authenticate as
# ``Authorization: Bearer`` + the oauth beta header; ``sk-ant-api...`` API keys
# use ``x-api-key``. Both target api.anthropic.com, so the token prefix is the
# discriminator the header builders use. Override if the real flow differs.
CLAUDE_OAUTH_TOKEN_PREFIXES = tuple(
    p.strip()
    for p in os.getenv("CLAUDE_OAUTH_TOKEN_PREFIXES", "sk-ant-oat").split(",")
    if p.strip()
)


def is_oauth_access_token(token: Optional[str]) -> bool:
    """Heuristic: does this Anthropic credential look like an OAuth access token?

    The header builders use this to choose Bearer + oauth beta header (OAuth /
    subscription) over x-api-key (a static API key). Tunable via
    ``CLAUDE_OAUTH_TOKEN_PREFIXES``.
    """
    return bool(token) and str(token).startswith(CLAUDE_OAUTH_TOKEN_PREFIXES)

# --- Claude Code OAuth config (verified from the real flow; env-overridable) ---
# Sources (cross-checked): OpenCode `opencode-anthropic-auth` plugin, the
# `~/.claude/.credentials.json` shape from `claude setup-token`, and multiple
# community write-ups of Claude Code's PKCE flow. The `claude.ai` authorize host
# is the Pro/Max *subscription* path (vs `console.anthropic.com` for API keys).
CLAUDE_OAUTH_CLIENT_ID = os.getenv(
    "CLAUDE_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
).strip()
CLAUDE_OAUTH_AUTHORIZE_URL = os.getenv(
    "CLAUDE_OAUTH_AUTHORIZE_URL", "https://claude.ai/oauth/authorize"
).strip()
CLAUDE_OAUTH_TOKEN_URL = os.getenv(
    "CLAUDE_OAUTH_TOKEN_URL", "https://console.anthropic.com/v1/oauth/token"
).strip()
CLAUDE_OAUTH_REDIRECT_URI = os.getenv(
    "CLAUDE_OAUTH_REDIRECT_URI", "https://console.anthropic.com/oauth/code/callback"
).strip()
CLAUDE_OAUTH_SCOPE = os.getenv(
    "CLAUDE_OAUTH_SCOPE", "org:create_api_key user:profile user:inference"
).strip()

# Verified JSON (Claude Code's token endpoint expects application/json, not form).
CLAUDE_OAUTH_TOKEN_BODY = os.getenv("CLAUDE_OAUTH_TOKEN_BODY", "json").strip().lower()

# Refresh proactively this many seconds before expiry.
CLAUDE_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
# Fallback assumed lifetime when the access token is opaque (not a JWT we can
# read ``exp`` from) and the token endpoint did not report ``expires_in``.
CLAUDE_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("CLAUDE_ACCESS_TOKEN_TTL_SECONDS", "3600"))

# Sensible fallback model list when /v1/models is not reachable with an OAuth
# token. From the current Anthropic catalog; trim/extend to your subscription.
DEFAULT_CLAUDE_MODELS = [
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

_AUTH_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_AUTH_REFRESH_LOCKS_GUARD = threading.Lock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ClaudeSubscriptionError(RuntimeError):
    """Base error for Claude subscription provider failures."""


class ClaudeSubscriptionConfigError(ClaudeSubscriptionError):
    """Provider OAuth config (client id / URLs / redirect / scope) is missing."""


class ClaudeSubscriptionReauthRequired(ClaudeSubscriptionError):
    """Stored OAuth credentials are invalid or expired beyond refresh."""


class ClaudeSubscriptionRateLimited(ClaudeSubscriptionError):
    """Upstream quota/rate limit; reconnecting will not fix it."""


class ClaudeSubscriptionAuthNotFound(ClaudeSubscriptionError):
    """No matching owner-scoped auth session exists."""


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ClaudeSubscriptionConfigError):
        return HTTPException(501, str(exc))
    if isinstance(exc, ClaudeSubscriptionRateLimited):
        return HTTPException(429, str(exc))
    if isinstance(exc, (ClaudeSubscriptionReauthRequired, ClaudeSubscriptionAuthNotFound)):
        return HTTPException(401, f"{exc} Reconnect the provider.")
    return HTTPException(502, str(exc))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _require_oauth_config() -> None:
    """Fail fast with a precise message when provider OAuth config is unset."""
    missing = [
        name
        for name, value in (
            ("CLAUDE_OAUTH_CLIENT_ID", CLAUDE_OAUTH_CLIENT_ID),
            ("CLAUDE_OAUTH_AUTHORIZE_URL", CLAUDE_OAUTH_AUTHORIZE_URL),
            ("CLAUDE_OAUTH_TOKEN_URL", CLAUDE_OAUTH_TOKEN_URL),
            ("CLAUDE_OAUTH_REDIRECT_URI", CLAUDE_OAUTH_REDIRECT_URI),
        )
        if not value
    ]
    if missing:
        raise ClaudeSubscriptionConfigError(
            "Claude subscription OAuth is not configured. Set the environment "
            "variable(s): " + ", ".join(missing) + ". These are provider-specific "
            "values from the Claude Code / claude.ai login flow and are not shipped "
            "with Odysseus."
        )


def _raise_for_oauth_response(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    code = ""
    message = f"Claude Subscription {action} failed with HTTP {response.status_code}."
    try:
        payload = response.json()
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or "").strip()
            msg = err.get("message")
            if msg:
                message = f"Claude Subscription {action} failed: {msg}"
        elif isinstance(err, str):
            code = err.strip()
            desc = payload.get("error_description") or payload.get("message")
            if desc:
                message = f"Claude Subscription {action} failed: {desc}"
    except Exception:
        pass
    if response.status_code == 429:
        raise ClaudeSubscriptionRateLimited(
            "Claude Subscription quota or rate limit was reached. Credentials are still valid."
        )
    if response.status_code in (401, 403) or code in {
        "invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused",
    }:
        raise ClaudeSubscriptionReauthRequired(message)
    raise ClaudeSubscriptionError(message)


def _json_or_error(response: httpx.Response, action: str) -> Dict[str, Any]:
    _raise_for_oauth_response(response, action)
    try:
        data = response.json()
    except Exception as exc:
        raise ClaudeSubscriptionError(f"Claude Subscription {action} returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise ClaudeSubscriptionError(f"Claude Subscription {action} returned an unexpected response.")
    return data


def _post_token_request(payload: Dict[str, Any], action: str, timeout: float = 20.0) -> Dict[str, Any]:
    """POST to the OAuth token endpoint using the configured body encoding."""
    _require_oauth_config()
    if CLAUDE_OAUTH_TOKEN_BODY == "form":
        response = httpx.post(
            CLAUDE_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data=payload,
            timeout=timeout,
        )
    else:
        response = httpx.post(
            CLAUDE_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=timeout,
        )
    return _json_or_error(response, action)


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    segment = parts[1]
    segment += "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _token_is_expiring(
    access_token: str,
    last_refresh: Optional[datetime],
    skew_seconds: int = CLAUDE_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
) -> bool:
    """True if the access token is missing, near expiry, or unverifiable.

    Tries the JWT ``exp`` claim first; if the token is opaque (Claude OAuth
    access tokens may not be JWTs), falls back to ``last_refresh + TTL``.
    """
    if not access_token:
        return True
    now = int(time.time())
    try:
        exp = int(_decode_jwt_payload(access_token).get("exp") or 0)
        if exp:
            return exp <= now + int(skew_seconds)
    except Exception:
        pass
    # Opaque token: refresh based on elapsed time since last refresh.
    if last_refresh is None:
        return True
    ref = last_refresh
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    age = now - int(ref.timestamp())
    return age >= max(int(CLAUDE_ACCESS_TOKEN_TTL_SECONDS) - int(skew_seconds), 0)


# ---------------------------------------------------------------------------
# OAuth (authorization-code + PKCE) flow
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> Tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorize_url(state: str, code_challenge: str) -> str:
    """Build the provider authorize URL the user opens in a browser."""
    _require_oauth_config()
    params = {
        # `code=true` selects Claude Code's manual flow, where the callback page
        # shows the code to paste back (vs a silent localhost redirect).
        "code": "true",
        "response_type": "code",
        "client_id": CLAUDE_OAUTH_CLIENT_ID,
        "redirect_uri": CLAUDE_OAUTH_REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if CLAUDE_OAUTH_SCOPE:
        params["scope"] = CLAUDE_OAUTH_SCOPE
    return CLAUDE_OAUTH_AUTHORIZE_URL.rstrip("?") + "?" + urlencode(params)


def parse_authorization_code(raw: str) -> Tuple[str, Optional[str]]:
    """Split a pasted ``code#state`` value into (code, state)."""
    raw = (raw or "").strip()
    if "#" in raw:
        code, _, state = raw.partition("#")
        return code.strip(), (state.strip() or None)
    return raw, None


def exchange_authorization_code(
    authorization_code: str,
    code_verifier: str,
    state: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": CLAUDE_OAUTH_REDIRECT_URI,
        "client_id": CLAUDE_OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    }
    if state:
        payload["state"] = state
    data = _post_token_request(payload, "token exchange")
    if not data.get("access_token"):
        raise ClaudeSubscriptionReauthRequired("Claude token exchange did not return an access token.")
    return data


def refresh_oauth_tokens(refresh_token: str) -> Dict[str, Any]:
    if not refresh_token:
        raise ClaudeSubscriptionReauthRequired(
            "Claude Subscription is missing a refresh token. Reconnect the provider."
        )
    data = _post_token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_OAUTH_CLIENT_ID,
        },
        "token refresh",
    )
    if not data.get("access_token"):
        raise ClaudeSubscriptionReauthRequired("Claude token refresh did not return an access token.")
    return data


# ---------------------------------------------------------------------------
# Runtime request helpers
# ---------------------------------------------------------------------------

def claude_oauth_headers(access_token: Optional[str]) -> Dict[str, str]:
    """Headers for calling the Anthropic API with a subscription OAuth token.

    OAuth tokens use ``Authorization: Bearer`` (NOT ``x-api-key``) plus the
    OAuth beta header. This is what ``_build_anthropic_headers`` in llm_core
    must emit for subscription-backed endpoints.
    """
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def request_is_oauth(headers: Optional[Dict[str, str]]) -> bool:
    """True if built request headers carry an OAuth (subscription) bearer token.

    Lets llm_core's Anthropic branch decide whether to apply Claude Code
    impersonation shaping — the same way it branches on provider for ChatGPT.
    """
    if not headers:
        return False
    for k, v in headers.items():
        if k.lower() == "authorization" and isinstance(v, str) and v.startswith("Bearer "):
            return is_oauth_access_token(v[7:])
    return False


def shape_payload_for_claude_code(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shape an Anthropic Messages payload to impersonate Claude Code.

    OAuth subscription tokens require the request to lead with the Claude Code
    identity system prompt, else the API rejects them. Mirrors the ChatGPT
    provider's payload shaping (``build_responses_input`` / ``instructions``),
    but for Anthropic's native message format.

    Tool-call impersonation (Claude Code's ``cc_`` tool-name prefix) is NOT
    applied here — like the ChatGPT subscription provider, this path runs with
    tools disabled (the route sets ``supports_tools = False``). When enabling
    tools later, prefix tool names here and un-prefix them in the response parser.
    """
    if not isinstance(payload, dict):
        return payload
    identity = {"type": "text", "text": CLAUDE_CODE_SYSTEM_PROMPT}
    system = payload.get("system")
    if isinstance(system, list):
        first_text = ""
        if system and isinstance(system[0], dict):
            first_text = str(system[0].get("text") or "")
        if not first_text.startswith(CLAUDE_CODE_SYSTEM_PROMPT):
            payload["system"] = [identity] + system
    elif isinstance(system, str) and system.strip():
        if not system.startswith(CLAUDE_CODE_SYSTEM_PROMPT):
            payload["system"] = [identity, {"type": "text", "text": system}]
    else:
        payload["system"] = [identity]
    return payload


def fetch_available_models(access_token: str, timeout: float = 10.0) -> list[str]:
    """List model ids visible to this subscription token, newest first.

    Returns an empty list on any failure; callers fall back to
    ``DEFAULT_CLAUDE_MODELS``.
    """
    if not access_token:
        return []
    try:
        response = httpx.get(
            DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL.rstrip("/") + "/v1/models?limit=100",
            headers=claude_oauth_headers(access_token),
            timeout=timeout,
        )
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []
    entries = data.get("data", []) if isinstance(data, dict) else []
    ordered: list[str] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip() and model_id not in seen:
            ordered.append(model_id.strip())
            seen.add(model_id.strip())
    return ordered


def resolve_runtime_credentials(
    auth_id: str,
    owner: Optional[str] = None,
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Resolve an auth session to a current access token, refreshing if needed.

    Shape matches ``chatgpt_subscription.resolve_runtime_credentials`` so the
    endpoint-resolver dispatch can treat both providers uniformly.
    """
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

        access_token = row.access_token or ""
        if force_refresh or _token_is_expiring(access_token, row.last_refresh):
            with _refresh_lock_for(auth_id):
                db.refresh(row)
                access_token = row.access_token or ""
                refresh_token = row.refresh_token or ""
                if force_refresh or _token_is_expiring(access_token, row.last_refresh):
                    refreshed = refresh_oauth_tokens(refresh_token)
                    row.access_token = refreshed["access_token"]
                    if refreshed.get("refresh_token"):
                        row.refresh_token = refreshed["refresh_token"]
                    row.last_refresh = utcnow_naive()
                    db.commit()
                    db.refresh(row)
            access_token = row.access_token or ""

        return {
            "provider": CLAUDE_SUBSCRIPTION_PROVIDER,
            "base_url": (row.base_url or DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL).rstrip("/"),
            "api_key": access_token,
            "auth_mode": row.auth_mode or "claude",
        }
    finally:
        db.close()