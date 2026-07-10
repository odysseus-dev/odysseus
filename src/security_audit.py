"""Security audit log helpers.

Provides a small, central interface for recording security-relevant
events (logins, password changes, token creation, admin actions, etc.)
to the database. All writes are best-effort: failures are logged but
never raise, so a transient DB problem can't break a login route.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

# Event categories for callers and the UI filter.
LOGIN_SUCCESS = "login.success"
LOGIN_FAILURE = "login.failure"
LOGOUT = "logout"
PASSWORD_CHANGE = "password.change"
PASSWORD_RESET = "password.reset"
USER_CREATED = "user.created"
USER_DELETED = "user.deleted"
USER_ADMIN_SET = "user.admin_set"
USER_ADMIN_REVOKE = "user.admin_revoke"
USER_RENAME = "user.rename"
TOKEN_CREATED = "token.created"
TOKEN_REVOKED = "token.revoked"
TOKEN_USED = "token.used"
SETUP_COMPLETED = "setup.completed"
MFA_ENABLED = "mfa.enabled"
MFA_DISABLED = "mfa.disabled"
MFA_VERIFY_FAILURE = "mfa.verify_failure"
PRIVILEGE_DENIED = "privilege.denied"
PRIVILEGES_UPDATED = "user.privileges_updated"
LDAP_SETTINGS_UPDATED = "auth.ldap_settings_updated"
SIGNUP_POLICY_UPDATED = "auth.signup_policy_updated"

_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "key", "authorization", "cookie")


def _clip(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _warn(message: str, *args: Any) -> None:
    """Logging must not violate the audit helper's never-raise contract."""
    try:
        logger.warning(message, *args)
    except Exception:
        pass


def _request_meta(request: Optional[Request]) -> tuple[Optional[str], Optional[str]]:
    """Extract (ip, user_agent) from a FastAPI request, tolerating None."""
    if request is None:
        return None, None
    try:
        client = request.client
        ip = client.host if client else None
    except Exception:
        ip = None
    try:
        user_agent = request.headers.get("user-agent")
    except Exception:
        user_agent = None
    return ip, user_agent


def log_security_event(
    event_type: str,
    actor: Optional[str] = None,
    target: Optional[str] = None,
    success: bool = True,
    detail: Optional[str] = None,
    request: Optional[Request] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Persist a security event. Returns the event id, or None on failure."""
    db_session = None
    try:
        # Lazy import to avoid circular startup dependencies. Everything after
        # entry is inside this boundary: audit failures must never change the
        # result of the security operation that has already completed.
        from core.database import SecurityEvent, SessionLocal, utcnow_naive

        ip, user_agent = _request_meta(request)
        event_id = uuid.uuid4().hex
        now = utcnow_naive()

        detail_parts: list[str] = []
        clipped_detail = _clip(detail, 2048)
        if clipped_detail:
            detail_parts.append(clipped_detail)
        if extra:
            for index, (key, value) in enumerate(extra.items()):
                if index >= 20:
                    break
                safe_key = _clip(key, 64) or "field"
                if any(part in safe_key.lower() for part in _SENSITIVE_KEY_PARTS):
                    safe_value = "[REDACTED]"
                else:
                    safe_value = _clip(value, 256) or ""
                detail_parts.append(f"{safe_key}={safe_value}")
        detail_text = _clip("; ".join(detail_parts), 4096)

        db_session = SessionLocal()
        db_session.add(
            SecurityEvent(
                id=event_id,
                event_type=_clip(event_type, 128) or "unknown",
                actor=_clip(actor, 256),
                target=_clip(target, 256),
                success=success,
                ip=_clip(ip, 64),
                user_agent=_clip(user_agent, 512),
                detail_text=detail_text,
                created_at=now,
            )
        )
        db_session.commit()
    except Exception as exc:
        _warn("Failed to write security event %s: %s", event_type, exc)
        try:
            if db_session:
                db_session.rollback()
        except Exception:
            pass
        return None
    finally:
        if db_session:
            try:
                db_session.close()
            except Exception:
                pass
    return event_id


async def log_security_event_async(*args: Any, **kwargs: Any) -> Optional[str]:
    """Offload an audit write so async request handlers never block on SQLite."""
    return await asyncio.to_thread(log_security_event, *args, **kwargs)
