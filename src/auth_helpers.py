"""Shared auth helpers used by all route files."""

from typing import Optional
from fastapi import Request, HTTPException


def get_current_user(request: Request) -> Optional[str]:
    """Get current username from request state (set by auth middleware)."""
    return getattr(request.state, 'current_user', None)


def effective_user(request: Request) -> Optional[str]:
    """The real human behind the request, for ownership/attribution.

    Cookie sessions resolve to the logged-in username. Bearer `ody_` callers
    (the mobile app) come through as the sandboxed pseudo-user "api" so they
    can't wander into cookie/user routes by default — but their token was
    minted by, and belongs to, a real owner stamped on
    request.state.api_token_owner. Routes that should attribute a token's
    actions to that owner (sessions, chat history) call this instead of
    get_current_user, so the phone sees and creates the SAME data as the
    owner's desktop UI rather than a separate "api"-owned silo.

    For cookie sessions this is identical to get_current_user, so swapping a
    route over is a no-op for browser users.
    """
    if getattr(request.state, "api_token", False):
        owner = getattr(request.state, "api_token_owner", None)
        if owner:
            return owner
    return get_current_user(request)


def require_user(request: Request) -> str:
    """FastAPI dependency: reject unauthenticated callers, even if upstream
    middleware was bypassed (LOCALHOST_BYPASS, AUTH_ENABLED=false, SSRF from
    a sibling service). Returns the resolved username, or "" in unconfigured
    first-run mode when the caller is on loopback.

    Use this on routes that touch user data so middleware misconfig can't
    open them up.
    """
    u = get_current_user(request)
    if u:
        return u
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    # Unconfigured / first-run mode: only allow loopback callers.
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    if host in ("127.0.0.1", "::1", "localhost"):
        return ""
    raise HTTPException(401, "Not authenticated")


def require_privilege(request: Request, key: str) -> str:
    """Reject callers whose `auth.json` privilege flag for `key` is False.
    Returns the username so the route handler can keep using it.

    Admins always have every privilege via `auth_manager.get_privileges`
    (which returns ADMIN_PRIVILEGES wholesale), so this is a no-op for
    them. In unauthenticated single-user mode (`require_user` returns ""),
    privileges aren't enforced.
    """
    user = require_user(request)
    if not user:
        return user
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return user
    try:
        privs = auth_mgr.get_privileges(user) or {}
    except Exception:
        return user
    # True = permitted; missing key defaults to permitted (unknown privileges
    # fail open — the UI gates display-side).
    if not privs.get(key, True):
        raise HTTPException(403, f"Your account is not allowed to {key.replace('_', ' ')}.")
    return user


def owner_filter(query, model_cls, user: str, *, include_shared: bool = True):
    """Filter `query` so only rows owned by `user` (and optionally null-owner
    'shared' rows) come through. No-op when `user` is empty (single-user
    mode). Returns the modified query."""
    if not user:
        return query
    if include_shared:
        return query.filter((model_cls.owner == user) | (model_cls.owner == None))  # noqa: E711
    return query.filter(model_cls.owner == user)
