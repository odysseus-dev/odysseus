"""ChatGPT subscription / Codex backend OAuth helpers.

This provider is intentionally separate from OpenAI API-key endpoints. It uses
OpenAI account OAuth device authorization, stores refresh tokens server-side,
and resolves a fresh bearer token at request time.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL = (
    os.getenv("CHATGPT_SUBSCRIPTION_BASE_URL", "").strip().rstrip("/")
    or "https://chatgpt.com/backend-api/codex"
)
CHATGPT_SUBSCRIPTION_PROVIDER = "chatgpt-subscription"
# Legacy single-account endpoint/auth name. Rows provisioned before
# multi-account support keep this name and stay functional.
CHATGPT_SUBSCRIPTION_LEGACY_NAME = "ChatGPT Subscription"
CHATGPT_ACCOUNT_LABEL_MAX_LENGTH = 40
# Read-only account usage (rate-limit windows) on the authenticated ChatGPT
# backend. Mirrors openai/codex ``backend-client`` ``PathStyle::ChatGptApi``:
# ``{base}/wham/usage`` where base is ``https://chatgpt.com/backend-api``.
CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CHATGPT_USAGE_TIMEOUT_SECONDS = 8.0
CHATGPT_USAGE_CACHE_TTL_SECONDS = 45.0
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


# ── Account labels ──────────────────────────────────────────────────────────

_LABEL_WHITESPACE_RE = re.compile(r"\s+")


def normalize_account_label(raw: Any) -> str:
    """Return a trimmed, display-safe account label ("" when absent).

    Labels are purely cosmetic: they never participate in authentication or
    authorization (stable auth/endpoint ids do). Control characters are
    stripped, whitespace collapsed and the length bounded so the label is safe
    to echo in Settings, the model picker and provenance metadata.
    """
    if raw is None:
        return ""
    text = str(raw)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = _LABEL_WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > CHATGPT_ACCOUNT_LABEL_MAX_LENGTH:
        raise ValueError(
            f"Account label must be at most {CHATGPT_ACCOUNT_LABEL_MAX_LENGTH} characters."
        )
    return text


def endpoint_name_for_label(label: str) -> str:
    """User-visible endpoint name for a ChatGPT account label."""
    label = (label or "").strip()
    if not label:
        return CHATGPT_SUBSCRIPTION_LEGACY_NAME
    return f"ChatGPT · {label}"


def account_label_from_name(name: Optional[str], fallback: str = "") -> str:
    """Inverse of :func:`endpoint_name_for_label` for display purposes."""
    text = (name or "").strip()
    if text.startswith("ChatGPT · "):
        return text[len("ChatGPT · "):].strip() or fallback
    if text == CHATGPT_SUBSCRIPTION_LEGACY_NAME:
        return fallback
    return text or fallback


def labels_conflict(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.casefold() == b.casefold()


KNOWN_CODEX_REASONING_LEVELS = [
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent"
]

STANDARD_CODEX_REASONING_LEVELS = [
    "low", "medium", "high", "xhigh", "max", "ultra"
]

DEFAULT_CHATGPT_MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "gpt-6-astra": {
        "default_reasoning_level": "low",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "gpt-5.6-sol": {
        "default_reasoning_level": "low",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "gpt-5.6-terra": {
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "gpt-5.6-luna": {
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "gpt-5.5": {
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "gpt-5.4": {
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "codex-auto-review": {
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
}

# Runtime cache of model metadata (updated dynamically whenever models are fetched)
CHATGPT_MODEL_CATALOG_CACHE: dict[str, dict[str, Any]] = dict(DEFAULT_CHATGPT_MODEL_CATALOG)


def _extract_reasoning_levels(item: dict) -> list[str]:
    raw_levels = item.get("supported_reasoning_levels") or item.get("supportedReasoningEfforts")
    if not isinstance(raw_levels, list):
        return []
    levels: list[str] = []
    for entry in raw_levels:
        if isinstance(entry, dict):
            effort = entry.get("effort") or entry.get("level") or entry.get("name")
            if effort and isinstance(effort, str):
                levels.append(effort.strip().lower())
        elif isinstance(entry, str) and entry.strip():
            levels.append(entry.strip().lower())
    return levels


def get_chatgpt_model_metadata(slug: str) -> Optional[dict[str, Any]]:
    slug = (slug or "").strip()
    if not slug:
        return None
    if slug in CHATGPT_MODEL_CATALOG_CACHE:
        return dict(CHATGPT_MODEL_CATALOG_CACHE[slug])
    for k, v in CHATGPT_MODEL_CATALOG_CACHE.items():
        if k.casefold() == slug.casefold():
            return dict(v)
    slug_lower = slug.lower()
    if any(pat in slug_lower for pat in ("gpt-6", "gpt-5.6", "gpt-5.5", "gpt-5.4", "codex")):
        return {
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": list(STANDARD_CODEX_REASONING_LEVELS),
        }
    return None


def validate_reasoning_effort(model: str, effort: Optional[str]) -> Optional[str]:
    """Validate reasoning effort against model's advertised levels.
    Returns None if default/empty/unsupported (fail-safe to omitting override)."""
    if not effort:
        return None
    effort_clean = str(effort).strip().lower()
    if effort_clean in {"", "default"}:
        return None
    meta = get_chatgpt_model_metadata(model)
    if not meta:
        return None
    supported = [lvl.lower() for lvl in meta.get("supported_reasoning_levels", [])]
    if effort_clean in supported:
        return effort_clean
    return None


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
        slug_clean = slug.strip()
        visibility = item.get("visibility", "")
        if isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}:
            continue
        levels = _extract_reasoning_levels(item)
        default_lvl = item.get("default_reasoning_level") or item.get("defaultReasoningEffort")
        if levels:
            CHATGPT_MODEL_CATALOG_CACHE[slug_clean] = {
                "default_reasoning_level": str(default_lvl).strip().lower() if default_lvl else (levels[0] if levels else "medium"),
                "supported_reasoning_levels": levels,
            }
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug_clean))
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
        elif isinstance(err, str):
            code = err.strip()
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
                    row.last_refresh = utcnow_naive()
                    db.commit()
                    db.refresh(row)
            access_token = row.access_token or ""

        return {
            "provider": CHATGPT_SUBSCRIPTION_PROVIDER,
            "base_url": (row.base_url or DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL).rstrip("/"),
            "api_key": access_token,
            "auth_mode": row.auth_mode or "chatgpt",
        }
    finally:
        db.close()


def find_owned_auth_session(db, auth_id: str, owner: Optional[str]):
    """Return the owner-scoped ChatGPT ProviderAuthSession row or None.

    OAuth credentials belong to exactly one owner, including the legacy
    anonymous owner. Labels are never used for lookup.
    """
    ProviderAuthSession, _SessionLocal, _now = _database_handles()
    auth_id = (auth_id or "").strip()
    if not auth_id:
        return None
    q = db.query(ProviderAuthSession).filter(
        ProviderAuthSession.id == auth_id,
        ProviderAuthSession.provider == CHATGPT_SUBSCRIPTION_PROVIDER,
    )
    return q.filter(ProviderAuthSession.owner == owner).first()


def chatgpt_account_id_from_token(access_token: str) -> Optional[str]:
    """Extract the ChatGPT account id claim from an access token, if any."""
    try:
        payload = _decode_jwt_payload(access_token)
    except Exception:
        return None
    auth_claims = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    return None


def usage_request_headers(access_token: str) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Odysseus ChatGPT Subscription",
        "Authorization": f"Bearer {access_token}",
    }
    account_id = chatgpt_account_id_from_token(access_token)
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


class ChatGPTUsageUnavailable(ChatGPTSubscriptionError):
    """Usage telemetry could not be read; the model endpoint is unaffected."""

    def __init__(self, reason: str, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (ValueError, OverflowError):
            return None
    return None


def _coerce_int(value: Any) -> Optional[int]:
    number = _coerce_number(value)
    if number is None:
        return None
    try:
        return int(number)
    except (OverflowError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def window_minutes_from_seconds(seconds: Any) -> Optional[int]:
    """Codex-compatible ceil(seconds / 60); None for missing/non-positive."""
    value = _coerce_int(seconds)
    if value is None or value <= 0:
        return None
    return (value + 59) // 60


def friendly_window_name(window_minutes: Optional[int]) -> str:
    """Compact label derived from the actual window length (never assumed)."""
    if not window_minutes or window_minutes <= 0:
        return "LIMIT"
    if window_minutes % (7 * 24 * 60) == 0:
        weeks = window_minutes // (7 * 24 * 60)
        return "WEEK" if weeks == 1 else f"{weeks}W"
    if window_minutes % (24 * 60) == 0:
        return f"{window_minutes // (24 * 60)}D"
    if window_minutes % 60 == 0:
        return f"{window_minutes // 60}H"
    return f"{window_minutes}M"


def normalize_usage_window(raw: Any, kind: str) -> Optional[Dict[str, Any]]:
    """Normalize one ``primary_window``/``secondary_window`` snapshot.

    Upstream fields (openai/codex ``RateLimitWindowSnapshot``): ``used_percent``,
    ``limit_window_seconds``, ``reset_after_seconds``, ``reset_at``. Absent
    values stay ``None``; nothing is invented.
    """
    if not isinstance(raw, dict):
        return None
    used_percent = _coerce_number(raw.get("used_percent"))
    if used_percent is not None:
        used_percent = max(0.0, min(100.0, used_percent))
    window_minutes = window_minutes_from_seconds(raw.get("limit_window_seconds"))
    if window_minutes is None:
        window_minutes = _coerce_int(raw.get("window_minutes"))
        if window_minutes is not None and window_minutes <= 0:
            window_minutes = None
    resets_at = _coerce_int(raw.get("reset_at"))
    if resets_at is None:
        resets_at = _coerce_int(raw.get("resets_at"))
    if resets_at is not None and resets_at <= 0:
        resets_at = None
    reset_after_seconds = _coerce_int(raw.get("reset_after_seconds"))
    if reset_after_seconds is not None and reset_after_seconds < 0:
        reset_after_seconds = None
    return {
        "kind": kind,
        "name": friendly_window_name(window_minutes),
        "used_percent": used_percent,
        "remaining_percent": (None if used_percent is None else round(100.0 - used_percent, 2)),
        "window_minutes": window_minutes,
        "resets_at": resets_at,
        "reset_after_seconds": reset_after_seconds,
    }


def _normalize_rate_limit_details(raw: Any) -> Dict[str, Any]:
    details = raw if isinstance(raw, dict) else {}
    windows: List[Dict[str, Any]] = []
    # Preserve new window kinds without assigning a duration to their names.
    keys = ["primary_window", "secondary_window"]
    keys.extend(key for key in details if key.endswith("_window") and key not in keys)
    for key in keys:
        window = normalize_usage_window(details.get(key), key[:-7])
        if window is not None:
            windows.append(window)
    allowed = details.get("allowed")
    limit_reached = details.get("limit_reached")
    return {
        "allowed": allowed if isinstance(allowed, bool) else None,
        "limit_reached": limit_reached if isinstance(limit_reached, bool) else None,
        "windows": windows,
    }


def normalize_usage_payload(payload: Any) -> Dict[str, Any]:
    """Normalize a ``GET /wham/usage`` JSON body into Odysseus' safe contract.

    Returns only non-credential fields. The main Codex limit is reported as
    ``limit_id == "codex"`` (as openai/codex does); each entry of
    ``additional_rate_limits`` becomes its own bucket keyed by
    ``metered_feature``. Unknown fields are ignored, unknown buckets kept.
    """
    if not isinstance(payload, dict):
        raise ChatGPTUsageUnavailable("malformed", "ChatGPT usage response was not a JSON object.")

    limits: List[Dict[str, Any]] = []
    main = _normalize_rate_limit_details(payload.get("rate_limit"))
    limits.append({
        "limit_id": "codex",
        "limit_name": None,
        "normal_model_slug": None,
        "allowed": main["allowed"],
        "limit_reached": main["limit_reached"],
        "windows": main["windows"],
    })

    additional = payload.get("additional_rate_limits")
    if isinstance(additional, list):
        for entry in additional:
            if not isinstance(entry, dict):
                continue
            details = _normalize_rate_limit_details(entry.get("rate_limit"))
            limit_name = _optional_str(entry.get("limit_name"))
            limit_id = _optional_str(entry.get("metered_feature")) or limit_name
            if not limit_id and not details["windows"]:
                continue
            limits.append({
                "limit_id": limit_id or "additional",
                "limit_name": limit_name,
                "normal_model_slug": _optional_str(entry.get("normal_model_slug")),
                "allowed": details["allowed"],
                "limit_reached": details["limit_reached"],
                "windows": details["windows"],
            })

    reached = payload.get("rate_limit_reached_type")
    if isinstance(reached, dict):
        reached = _optional_str(reached.get("type") or reached.get("kind"))
    else:
        reached = _optional_str(reached)

    ordinary_usage_allowed = main["allowed"]
    return {
        "account_id": _optional_str(payload.get("account_id")),
        "plan_type": _optional_str(payload.get("plan_type")),
        "ordinary_usage_allowed": ordinary_usage_allowed,
        "rate_limit_reached_type": reached,
        "limits": limits,
    }


def fetch_usage_payload(access_token: str, timeout: float = CHATGPT_USAGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Read the raw usage JSON for one access token; classify failures."""
    if not access_token:
        raise ChatGPTUsageUnavailable("reauth", "ChatGPT Subscription has no access token.")
    try:
        response = httpx.get(CHATGPT_USAGE_URL, headers=usage_request_headers(access_token), timeout=timeout)
    except httpx.TimeoutException as exc:
        raise ChatGPTUsageUnavailable("timeout", "ChatGPT usage request timed out.") from exc
    except httpx.HTTPError as exc:
        raise ChatGPTUsageUnavailable("network", "ChatGPT usage request failed.") from exc
    status = response.status_code
    if status in (401, 403):
        raise ChatGPTUsageUnavailable(
            "reauth",
            "ChatGPT rejected the usage request; the account may need reconnecting.",
            status_code=status,
        )
    if status == 429:
        raise ChatGPTUsageUnavailable("rate_limited", "ChatGPT usage is temporarily rate limited.", status_code=status)
    if status >= 500:
        raise ChatGPTUsageUnavailable("upstream", f"ChatGPT usage service returned HTTP {status}.", status_code=status)
    if status != 200:
        raise ChatGPTUsageUnavailable("upstream", f"ChatGPT usage request returned HTTP {status}.", status_code=status)
    try:
        data = response.json()
    except Exception as exc:
        raise ChatGPTUsageUnavailable("malformed", "ChatGPT usage response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ChatGPTUsageUnavailable("malformed", "ChatGPT usage response was not a JSON object.")
    return data


class UsageCache:
    """Short per-auth-session cache for normalized usage snapshots."""

    def __init__(self, ttl_seconds: float = CHATGPT_USAGE_CACHE_TTL_SECONDS, time_func=time.monotonic, max_entries: int = 256):
        self._ttl = float(ttl_seconds)
        self._max_entries = max(1, int(max_entries))
        self._time = time_func
        self._entries: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def get(self, auth_id: str) -> Optional[Dict[str, Any]]:
        now = float(self._time())
        with self._lock:
            entry = self._entries.get(auth_id)
            if entry is None:
                return None
            stored_at, value = entry
            if now - stored_at >= self._ttl:
                self._entries.pop(auth_id, None)
                return None
            return json.loads(json.dumps(value))

    def put(self, auth_id: str, value: Dict[str, Any]) -> None:
        with self._lock:
            now = float(self._time())
            for key, (stored_at, _) in list(self._entries.items()):
                if now - stored_at >= self._ttl:
                    self._entries.pop(key, None)
            self._entries.pop(auth_id, None)
            while len(self._entries) >= self._max_entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[auth_id] = (now, json.loads(json.dumps(value)))

    def invalidate(self, auth_id: str) -> None:
        with self._lock:
            self._entries.pop(auth_id, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


USAGE_CACHE = UsageCache()


def get_account_usage(
    auth_id: str,
    owner: Optional[str] = None,
    *,
    force_refresh: bool = False,
    cache: Optional[UsageCache] = None,
) -> Dict[str, Any]:
    """Return normalized usage for exactly one owner-scoped auth session.

    The access token is resolved (and refreshed if needed) for that auth
    session only. Results are cached per auth id; ``force_refresh`` bypasses
    and replaces the cached entry. Raises :class:`ChatGPTUsageUnavailable` on
    any read failure; callers must not treat that as an endpoint failure.
    """
    cache = USAGE_CACHE if cache is None else cache
    auth_id = (auth_id or "").strip()
    if not auth_id:
        raise ChatGPTSubscriptionAuthNotFound("ChatGPT Subscription account was not found.")
    # Authorize even cache hits: cached telemetry must not outlive ownership.
    _Auth, SessionLocal, _now = _database_handles()
    db = SessionLocal()
    try:
        if find_owned_auth_session(db, auth_id, owner) is None:
            raise ChatGPTSubscriptionAuthNotFound("ChatGPT Subscription account was not found.")
    finally:
        db.close()
    if not force_refresh:
        cached = cache.get(auth_id)
        if cached is not None:
            cached["cached"] = True
            return cached
    else:
        cache.invalidate(auth_id)
    try:
        creds = resolve_runtime_credentials(auth_id, owner=owner)
    except ChatGPTSubscriptionAuthNotFound:
        raise
    except ChatGPTSubscriptionRateLimited as exc:
        raise ChatGPTUsageUnavailable("rate_limited", str(exc), status_code=429) from exc
    except ChatGPTSubscriptionReauthRequired as exc:
        raise ChatGPTUsageUnavailable("reauth", str(exc), status_code=401) from exc
    except ChatGPTSubscriptionError as exc:
        raise ChatGPTUsageUnavailable("upstream", str(exc)) from exc
    raw = fetch_usage_payload(creds.get("api_key") or "")
    normalized = normalize_usage_payload(raw)
    normalized["auth_id"] = auth_id
    normalized["fetched_at"] = int(time.time())
    cache.put(auth_id, normalized)
    result = json.loads(json.dumps(normalized))
    result["cached"] = False
    return result


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ChatGPTSubscriptionRateLimited):
        return HTTPException(429, str(exc))
    if isinstance(exc, (ChatGPTSubscriptionReauthRequired, ChatGPTSubscriptionAuthNotFound)):
        return HTTPException(401, f"{exc} Reconnect the provider.")
    if isinstance(exc, (ChatGPTSubscriptionError, ValueError)):
        return HTTPException(502, str(exc))
    return HTTPException(502, "ChatGPT Subscription request failed.")


# Content-part types that carry an image, across the formats that reach here:
# Odysseus builds OpenAI chat-completions style ("image_url"), while some
# producers already emit Responses-style ("input_image") or a bare "image".
_IMAGE_PART_TYPES = ("image_url", "input_image", "image")


def _responses_image_url(part: dict) -> Optional[str]:
    """Extract a usable image URL from a content part, or None.

    The Responses API wants ``image_url`` as a bare string. The chat-completions
    format that build_user_content produces wraps it as ``{"url": ...}``; passing
    that object through is rejected with
    ``Invalid type for 'input[N].content[M].image_url': expected an image URL,
    but got an object instead.``
    """
    raw = part.get("image_url")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        url = raw.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    img = part.get("image")
    if isinstance(img, str) and img.strip():
        return img.strip()
    return None


def build_responses_input(messages: list[dict]) -> list[dict]:
    input_items: list[dict] = []
    for msg in messages or []:
        role = msg.get("role") or "user"
        if role == "tool":
            role = "user"
        content = msg.get("content")
        images: list[str] = []
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                # Image parts carry no "text"/"content" key, so folding them
                # into the text join silently discarded the attachment and the
                # model answered "No image was provided". Convert them instead.
                if part.get("type") in _IMAGE_PART_TYPES:
                    url = _responses_image_url(part)
                    if url:
                        images.append(url)
                    continue
                texts.append(str(part.get("text") or part.get("content") or ""))
            text = "\n".join(texts)
        else:
            text = "" if content is None else str(content)
        input_type = "output_text" if role == "assistant" else "input_text"
        parts: list[dict] = [{"type": input_type, "text": text}]
        # Only an input turn may carry an image; an assistant turn's content has
        # to stay output_text, so drop images there rather than send a shape the
        # API rejects.
        if images and input_type == "input_text":
            parts.extend({"type": "input_image", "image_url": url} for url in images)
        input_items.append({"role": role, "content": parts})
    return input_items
