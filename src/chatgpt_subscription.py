"""ChatGPT subscription / Codex backend OAuth helpers.

This provider is intentionally separate from OpenAI API-key endpoints. It uses
OpenAI account OAuth device authorization, stores refresh tokens server-side,
and resolves a fresh bearer token at request time.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL = (
    os.getenv("CHATGPT_SUBSCRIPTION_BASE_URL", "").strip().rstrip("/")
    or "https://chatgpt.com/backend-api/codex"
)
CHATGPT_SUBSCRIPTION_PROVIDER = "chatgpt-subscription"
CHATGPT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_OAUTH_ISSUER = "https://auth.openai.com"
CHATGPT_OAUTH_REDIRECT_URI = f"{CHATGPT_OAUTH_ISSUER}/deviceauth/callback"
CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
_AUTH_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_AUTH_REFRESH_LOCKS_GUARD = threading.Lock()
_ACCOUNT_IDS_BY_ACCESS_TOKEN: dict[str, str] = {}
_ACCOUNT_IDS_GUARD = threading.Lock()


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


def _remember_account_for_access_token(access_token: Optional[str], account_id: Optional[str]) -> None:
    if not access_token or not account_id:
        return
    with _ACCOUNT_IDS_GUARD:
        _ACCOUNT_IDS_BY_ACCESS_TOKEN[str(access_token)] = str(account_id)


def remember_chatgpt_account_id(access_token: Optional[str], account_id: Optional[str]) -> None:
    _remember_account_for_access_token(access_token, account_id)


def _remembered_account_for_access_token(access_token: Optional[str]) -> Optional[str]:
    if not access_token:
        return None
    with _ACCOUNT_IDS_GUARD:
        return _ACCOUNT_IDS_BY_ACCESS_TOKEN.get(str(access_token))


def chatgpt_headers(access_token: Optional[str], account_id: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/codex",
        "User-Agent": "Odysseus ChatGPT Subscription",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    resolved_account_id = account_id or _remembered_account_for_access_token(access_token)
    if resolved_account_id:
        headers["ChatGPT-Account-Id"] = str(resolved_account_id)
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


def request_device_code(timeout: float = 15.0) -> Dict[str, Any]:
    response = httpx.post(
        f"{CHATGPT_OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": CHATGPT_OAUTH_CLIENT_ID},
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


def _chatgpt_account_id_from_jwt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        payload = _decode_jwt_payload(token)
    except Exception:
        return None
    auth_claims = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    for key in ("chatgpt_account_id", "account_id"):
        account_id = payload.get(key)
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    return None


def chatgpt_account_id_from_tokens(tokens: Dict[str, Any]) -> Optional[str]:
    for key in ("account_id", "chatgpt_account_id"):
        account_id = tokens.get(key)
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    return _chatgpt_account_id_from_jwt(tokens.get("id_token")) or _chatgpt_account_id_from_jwt(tokens.get("access_token"))


def access_token_is_expiring(access_token: str, skew_seconds: int = CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    try:
        exp = int(_decode_jwt_payload(access_token).get("exp") or 0)
    except Exception:
        return True
    return exp <= int(time.time()) + int(skew_seconds)


def resolve_runtime_credentials(auth_id: str, owner: Optional[str] = None, *, force_refresh: bool = False) -> Dict[str, Any]:
    ProviderAuthSession, SessionLocal, utcnow_naive = _database_handles()
    db = SessionLocal()
    try:
        q = db.query(ProviderAuthSession).filter(
            ProviderAuthSession.id == auth_id,
            ProviderAuthSession.provider == CHATGPT_SUBSCRIPTION_PROVIDER,
        )
        if owner:
            q = q.filter(ProviderAuthSession.owner == owner)
        row = q.first()
        if row is None:
            raise ChatGPTSubscriptionAuthNotFound("ChatGPT Subscription credentials were not found for this user.")

        access_token = row.access_token or ""
        if force_refresh or access_token_is_expiring(access_token):
            with _refresh_lock_for(auth_id):
                db.refresh(row)
                access_token = row.access_token or ""
                refresh_token = row.refresh_token or ""
                if force_refresh or access_token_is_expiring(access_token):
                    refreshed = refresh_oauth_tokens(access_token, refresh_token)
                    row.access_token = refreshed["access_token"]
                    if refreshed.get("refresh_token"):
                        row.refresh_token = refreshed["refresh_token"]
                    account_id = chatgpt_account_id_from_tokens(refreshed)
                    if account_id:
                        row.chatgpt_account_id = account_id
                    row.last_refresh = utcnow_naive()
                    db.commit()
                    db.refresh(row)
            access_token = row.access_token or ""

        account_id = (getattr(row, "chatgpt_account_id", None) or "").strip() or chatgpt_account_id_from_tokens({"access_token": access_token})
        if account_id:
            _remember_account_for_access_token(access_token, account_id)
        return {
            "provider": CHATGPT_SUBSCRIPTION_PROVIDER,
            "base_url": (row.base_url or DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL).rstrip("/"),
            "api_key": access_token,
            "auth_mode": row.auth_mode or "chatgpt",
            "chatgpt_account_id": account_id,
        }
    finally:
        db.close()


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ChatGPTSubscriptionRateLimited):
        return HTTPException(429, str(exc))
    if isinstance(exc, (ChatGPTSubscriptionReauthRequired, ChatGPTSubscriptionAuthNotFound)):
        return HTTPException(401, f"{exc} Reconnect the provider.")
    return HTTPException(502, str(exc))


def build_responses_input(messages: list[dict]) -> list[dict]:
    def _content_text(content: Any) -> str:
        if isinstance(content, list):
            return "\n".join(
                str(part.get("text") or part.get("content") or "")
                for part in content
                if isinstance(part, dict)
            )
        return "" if content is None else str(content)

    def _tool_call_item(tool_call: dict) -> Optional[dict]:
        if not isinstance(tool_call, dict):
            return None
        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        name = str(fn.get("name") or tool_call.get("name") or "").strip()
        if not name:
            return None
        arguments = fn.get("arguments", tool_call.get("arguments", "{}"))
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {})
        call_id = str(tool_call.get("id") or tool_call.get("call_id") or "").strip()
        if not call_id:
            call_id = f"call_{len(input_items)}"
        return {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
        }

    input_items: list[dict] = []
    for msg in messages or []:
        role = msg.get("role") or "user"
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "").strip()
            if call_id:
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _content_text(msg.get("content")),
                })
            continue
        text = _content_text(msg.get("content"))
        input_type = "output_text" if role == "assistant" else "input_text"
        if text or role != "assistant" or not msg.get("tool_calls"):
            input_items.append({"role": role, "content": [{"type": input_type, "text": text}]})
        if role == "assistant":
            for tool_call in msg.get("tool_calls") or []:
                item = _tool_call_item(tool_call)
                if item:
                    input_items.append(item)
    return input_items


def build_responses_tools(tools: list[dict] | None) -> list[dict]:
    """Convert OpenAI chat-completions tool schemas to Responses function tools."""
    response_tools: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            item: dict[str, Any] = {
                "type": "function",
                "name": name,
                "parameters": fn.get("parameters") or {},
            }
            if fn.get("description"):
                item["description"] = str(fn["description"])
            if "strict" in fn:
                item["strict"] = bool(fn["strict"])
            elif "strict" in tool:
                item["strict"] = bool(tool["strict"])
            response_tools.append(item)
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            response_tools.append(dict(tool))
    return response_tools
