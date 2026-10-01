"""Provider connection projection for 9router-hosted OAuth.

Odysseus starts connect UX and stores an opaque connection id plus non-secret
status. 9router keeps access and refresh tokens. This module must not return
upstream OAuth secrets to callers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL = (
    os.getenv("CHATGPT_SUBSCRIPTION_BASE_URL", "").strip().rstrip("/")
    or "https://chatgpt.com/backend-api/codex"
)
CHATGPT_SUBSCRIPTION_PROVIDER = "chatgpt-subscription"
NINEROUTER_CONNECTION_PROVIDER = "9router"
CHATGPT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_OAUTH_ISSUER = "https://auth.openai.com"
CHATGPT_OAUTH_REDIRECT_URI = f"{CHATGPT_OAUTH_ISSUER}/deviceauth/callback"
CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
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


class ChatGPTSubscriptionError(RuntimeError):
    """Base error for ChatGPT subscription provider failures."""


class ChatGPTSubscriptionReauthRequired(ChatGPTSubscriptionError):
    """Stored OAuth credentials are invalid or expired beyond refresh."""


class ChatGPTSubscriptionRateLimited(ChatGPTSubscriptionError):
    """Upstream quota/rate limit; reconnecting will not fix it."""


class ChatGPTSubscriptionAuthNotFound(ChatGPTSubscriptionError):
    """No matching owner-scoped auth session exists."""


def is_chatgpt_subscription_base(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower().rstrip(".")
        path = (parsed.path or "").rstrip("/")
    except Exception:
        return False
    return host == "chatgpt.com" and (
        path == "/backend-api/codex" or path.startswith("/backend-api/codex/")
    )


def chatgpt_headers(access_token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/codex",
        "User-Agent": "Odysseus ChatGPT Subscription",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def fetch_available_models(access_token: str, timeout: float = 10.0) -> list[str]:
    if not access_token:
        return []
    try:
        response = httpx.get(
            "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0",
            headers=chatgpt_headers(access_token),
            timeout=timeout,
        )
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []
    entries = data.get("models", []) if isinstance(data, dict) else []
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        visibility = item.get("visibility", "")
        if isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug.strip()))
    sortable.sort(key=lambda item: (item[0], item[1]))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, slug in sortable:
        if slug not in seen:
            ordered.append(slug)
            seen.add(slug)
    return ordered


def _raise_for_oauth_response(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    code = ""
    message = f"ChatGPT Subscription {action} failed with HTTP {response.status_code}."
    try:
        payload = response.json()
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or "").strip()
            msg = err.get("message")
            if msg:
                message = f"ChatGPT Subscription {action} failed: {msg}"
        elif isinstance(err, str):
            code = err.strip()
            desc = payload.get("error_description") or payload.get("message")
            if desc:
                message = f"ChatGPT Subscription {action} failed: {desc}"
    except Exception:
        pass
    if response.status_code == 429:
        raise ChatGPTSubscriptionRateLimited(
            "ChatGPT Subscription quota or rate limit was reached. Credentials are still valid."
        )
    if response.status_code in (401, 403) or code in {"invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused"}:
        raise ChatGPTSubscriptionReauthRequired(message)
    raise ChatGPTSubscriptionError(message)


def _json_or_error(response: httpx.Response, action: str) -> Dict[str, Any]:
    _raise_for_oauth_response(response, action)
    try:
        data = response.json()
    except Exception as exc:
        raise ChatGPTSubscriptionError(f"ChatGPT Subscription {action} returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise ChatGPTSubscriptionError(f"ChatGPT Subscription {action} returned an unexpected response.")
    return data


def make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge) for ChatGPT device auth.

    Agents: verifier stays in the in-memory poll store only. Never write it to
    SQLite or return it in /device/start JSON.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def request_device_code(timeout: float = 15.0, code_challenge: str | None = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"client_id": CHATGPT_OAUTH_CLIENT_ID}
    if code_challenge:
        body["code_challenge"] = code_challenge
        body["code_challenge_method"] = "S256"
    response = httpx.post(
        f"{CHATGPT_OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    data = _json_or_error(response, "device-code request")
    if not data.get("device_auth_id") or not data.get("user_code"):
        raise ChatGPTSubscriptionError("ChatGPT device-code response was missing required fields.")
    data.setdefault("verification_uri", f"{CHATGPT_OAUTH_ISSUER}/codex/device")
    data.setdefault("interval", 5)
    data.setdefault("expires_in", 900)
    return data


def poll_device_auth(device_auth_id: str, user_code: str, timeout: float = 15.0) -> Dict[str, Any]:
    response = httpx.post(
        f"{CHATGPT_OAUTH_ISSUER}/api/accounts/deviceauth/token",
        json={"device_auth_id": device_auth_id, "user_code": user_code},
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if response.status_code in (403, 404):
        return {"status": "pending", "error": "authorization_pending"}
    return _json_or_error(response, "device-code poll")


def exchange_authorization_code(authorization_code: str, code_verifier: str, timeout: float = 15.0) -> Dict[str, Any]:
    response = httpx.post(
        CHATGPT_OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": CHATGPT_OAUTH_REDIRECT_URI,
            "client_id": CHATGPT_OAUTH_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        timeout=timeout,
    )
    data = _json_or_error(response, "token exchange")
    if not data.get("access_token"):
        raise ChatGPTSubscriptionReauthRequired("ChatGPT token exchange did not return an access token.")
    return data


def refresh_oauth_tokens(access_token: str, refresh_token: str, timeout: float = 20.0) -> Dict[str, Any]:
    del access_token
    if not refresh_token:
        raise ChatGPTSubscriptionReauthRequired("ChatGPT Subscription is missing a refresh token. Reconnect the provider.")
    response = httpx.post(
        CHATGPT_OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CHATGPT_OAUTH_CLIENT_ID,
        },
        timeout=timeout,
    )
    data = _json_or_error(response, "token refresh")
    if not data.get("access_token"):
        raise ChatGPTSubscriptionReauthRequired("ChatGPT token refresh did not return an access token.")
    return data


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    segment = parts[1]
    segment += "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def access_token_is_expiring(access_token: str, skew_seconds: int = CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    try:
        exp = int(_decode_jwt_payload(access_token).get("exp") or 0)
    except Exception:
        return True
    return exp <= int(time.time()) + int(skew_seconds)


def ninerouter_public_url() -> str:
    """Return the 9router origin used for hosted PKCE redirects.

    Returns
    -------
    str
        Public or overlay origin without a trailing slash.

    Examples
    --------
    >>> ninerouter_public_url().startswith("http")
    True
    """
    return (
        os.getenv("NINE_ROUTER_PUBLIC_URL", "").strip().rstrip("/")
        or os.getenv("NINE_ROUTER_METADATA_URL", "").strip().rstrip("/")
        or "http://9router:20128"
    )


def _projection_payload(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "connection_id": row.connection_id,
        "owner": row.owner,
        "status": row.status or "usable",
        "entitlement": row.entitlement,
        "label": row.label,
    }


def provision_connection(projection: Dict[str, Any], owner: Optional[str]) -> Dict[str, Any]:
    """Persist an owner-scoped 9router connection without provider secrets.

    Parameters
    ----------
    projection
        Opaque connection fields. ``connection_id`` is required. Token fields
        are ignored and never written.
    owner
        Odysseus owner that consented.

    Returns
    -------
    dict
        Non-secret projection returned to product UX.

    Raises
    ------
    ValueError
        If ``connection_id`` is missing.

    Examples
    --------
    >>> isinstance(provision_connection, object)
    True
    """
    connection_id = str(projection.get("connection_id") or "").strip()
    if not connection_id:
        raise ValueError("connection_id is required; Odysseus no longer stores provider tokens")

    ProviderAuthSession, SessionLocal, utcnow_naive = _database_handles()
    db = SessionLocal()
    try:
        auth = (
            db.query(ProviderAuthSession)
            .filter(
                ProviderAuthSession.connection_id == connection_id,
                ProviderAuthSession.owner == owner,
            )
            .first()
        )
        if auth is None:
            auth = ProviderAuthSession(
                id=str(uuid.uuid4())[:8],
                provider=NINEROUTER_CONNECTION_PROVIDER,
                owner=owner,
                label=str(projection.get("label") or "9router"),
                base_url=NINEROUTER_CONNECTION_PROVIDER,
                auth_mode="projection",
            )
            db.add(auth)
        auth.connection_id = connection_id
        auth.status = str(projection.get("status") or "usable")
        auth.entitlement = projection.get("entitlement")
        auth.label = str(projection.get("label") or auth.label or "9router")
        auth.provider = NINEROUTER_CONNECTION_PROVIDER
        auth.base_url = NINEROUTER_CONNECTION_PROVIDER
        auth.auth_mode = "projection"
        auth.access_token = None
        auth.refresh_token = None
        auth.last_refresh = utcnow_naive()
        db.commit()
        db.refresh(auth)
        return _projection_payload(auth)
    finally:
        db.close()


def get_owner_connection(connection_id: str, owner: Optional[str]) -> Dict[str, Any]:
    """Load a connection projection for one owner only.

    Parameters
    ----------
    connection_id
        Opaque 9router connection id.
    owner
        Odysseus owner that must match the stored row.

    Returns
    -------
    dict
        Non-secret projection.

    Raises
    ------
    ChatGPTSubscriptionAuthNotFound
        If the id is missing or belongs to another owner.

    Examples
    --------
    >>> callable(get_owner_connection)
    True
    """
    if not connection_id or not owner:
        raise ChatGPTSubscriptionAuthNotFound("Provider connection was not found for this user.")
    ProviderAuthSession, SessionLocal, _utcnow = _database_handles()
    db = SessionLocal()
    try:
        row = (
            db.query(ProviderAuthSession)
            .filter(
                ProviderAuthSession.connection_id == connection_id,
                ProviderAuthSession.owner == owner,
            )
            .first()
        )
        if row is None:
            raise ChatGPTSubscriptionAuthNotFound("Provider connection was not found for this user.")
        return _projection_payload(row)
    finally:
        db.close()


def resolve_runtime_credentials(auth_id: str, owner: Optional[str] = None, *, force_refresh: bool = False) -> Dict[str, Any]:
    """Return a non-secret projection. Never emit upstream OAuth tokens.

    Parameters
    ----------
    auth_id
        Odysseus ``ProviderAuthSession.id``.
    owner
        Optional owner scope.
    force_refresh
        Ignored. Odysseus is not the token vault.

    Returns
    -------
    dict
        Projection without ``api_key`` / access / refresh values.

    Raises
    ------
    ChatGPTSubscriptionAuthNotFound
        If the row is missing or owned by someone else.

    Examples
    --------
    >>> callable(resolve_runtime_credentials)
    True
    """
    del force_refresh
    ProviderAuthSession, SessionLocal, _utcnow = _database_handles()
    db = SessionLocal()
    try:
        q = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == auth_id)
        if owner:
            q = q.filter(ProviderAuthSession.owner == owner)
        row = q.first()
        if row is None:
            raise ChatGPTSubscriptionAuthNotFound("Provider connection was not found for this user.")
        payload = _projection_payload(row)
        payload.update({
            "provider": row.provider or NINEROUTER_CONNECTION_PROVIDER,
            "base_url": "",
            "api_key": None,
            "auth_mode": row.auth_mode or "projection",
        })
        return payload
    finally:
        db.close()


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ChatGPTSubscriptionRateLimited):
        return HTTPException(429, str(exc))
    if isinstance(exc, (ChatGPTSubscriptionReauthRequired, ChatGPTSubscriptionAuthNotFound)):
        return HTTPException(401, f"{exc} Reconnect the provider.")
    return HTTPException(502, str(exc))


def build_responses_input(messages: list[dict]) -> list[dict]:
    input_items: list[dict] = []
    for msg in messages or []:
        role = msg.get("role") or "user"
        if role == "tool":
            role = "user"
        content = msg.get("content")
        if isinstance(content, list):
            text = "\n".join(str(part.get("text") or part.get("content") or "") for part in content if isinstance(part, dict))
        else:
            text = "" if content is None else str(content)
        input_type = "output_text" if role == "assistant" else "input_text"
        input_items.append({"role": role, "content": [{"type": input_type, "text": text}]})
    return input_items
