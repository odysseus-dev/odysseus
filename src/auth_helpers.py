"""Shared auth helpers used by all route files."""

import os
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, HTTPException

from src.owner_identity import (
    auth_disabled,
    effective_storage_owner,
    is_request_sentinel_owner,
)


@dataclass(frozen=True)
class RequestCapability:
    """Immutable request authority passed through chat execution helpers.

    A bearer token that has the narrow ``chat`` scope is still a pure chat
    capability.  It may complete the synchronous model call, but it cannot
    create detached execution, emit interactive events, or schedule follow-up
    work that would run after the request's authorization context is gone.
    Cookie and AUTH_ENABLED=false requests retain the existing interactive
    behavior.
    """

    principal: str
    owner: Optional[str]
    is_bearer: bool
    allow_deferred_work: bool
    allow_detached_execution: bool
    allow_message_events: bool
    allow_auto_naming: bool


def is_bearer_principal(request: Request) -> bool:
    """Return whether the request is attributable to an API-token principal.

    The auth middleware stamps ``state.api_token`` for a verified token.  The
    header/sentinel checks keep direct endpoint calls and auth-disabled
    alternate entry points fail-closed instead of treating the ``api``
    sentinel as a normal cookie user.
    """
    state = getattr(request, "state", None)
    if getattr(state, "api_token", False) is True:
        return True
    current_user = getattr(state, "current_user", None)
    if isinstance(current_user, str) and current_user.strip().casefold() == "api":
        return True
    try:
        auth_header = request.headers.get("authorization", "")
    except Exception:
        auth_header = ""
    return isinstance(auth_header, str) and auth_header.strip().casefold().startswith("bearer ody_")


def get_current_user(request: Request) -> Optional[str]:
    """Get current username from request state (set by auth middleware)."""
    state = getattr(request, "state", None)
    return getattr(state, "current_user", None)


def effective_user(request: Request) -> Optional[str]:
    """The real human behind the request, for ownership/attribution.

    Cookie sessions resolve to the logged-in username. Bearer ``ody_`` callers
    come through as the sandboxed pseudo-user "api" so they can't wander into
    cookie/user routes by default, but their token was minted by, and belongs
    to, a real owner stamped on ``request.state.api_token_owner``. Routes that
    should attribute a token's actions to that owner (sessions, chat history)
    call this instead of :func:`get_current_user`, so a paired client sees and
    creates the SAME data as the owner's desktop UI rather than a separate
    "api"-owned silo.

    For cookie sessions this is identical to :func:`get_current_user`, so
    swapping a route over is a no-op for browser users. A bearer token with no
    owner falls back to :func:`get_current_user` (the "api" pseudo-user), so it
    never escalates.
    """
    if _is_api_token_request(request):
        state = getattr(request, "state", None)
        owner = getattr(state, "api_token_owner", None)
        if isinstance(owner, str) and owner.strip():
            return owner.strip()
    return get_current_user(request)


def _is_api_token_request(request: Request) -> bool:
    """Return True when the request has a bearer API-token principal."""
    return is_bearer_principal(request)


def request_capability(request: Request) -> RequestCapability:
    """Build the one request capability shared by chat downstream helpers."""
    bearer = is_bearer_principal(request)
    return RequestCapability(
        principal="bearer" if bearer else "interactive",
        owner=effective_user(request),
        is_bearer=bearer,
        allow_deferred_work=not bearer,
        allow_detached_execution=not bearer,
        allow_message_events=not bearer,
        allow_auto_naming=not bearer,
    )


def require_api_token_owner(request: Request) -> str:
    """Return a real owner for a bearer request, failing closed otherwise.

    The middleware normally resolves token owners against configured human
    accounts. Keep that invariant at route boundaries too: direct endpoint
    tests, alternate ASGI entry points, and future middleware changes must not
    turn a request sentinel or an ownerless token into a durable/executable
    owner.
    """
    state = getattr(request, "state", None)
    owner = getattr(state, "api_token_owner", None)
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or is_request_sentinel_owner(owner)
    ):
        raise HTTPException(403, "API token has no owner")
    normalized_owner = owner.strip()
    # The normal auth middleware has already resolved this identity from the
    # token row. Keep the same invariant for direct endpoint calls and
    # alternate ASGI entry points when a configured auth manager is available.
    auth_state = getattr(getattr(request, "app", None), "state", None)
    auth_manager = getattr(auth_state, "auth_manager", None)
    users = getattr(auth_manager, "users", None)
    if (
        getattr(auth_manager, "is_configured", False)
        and isinstance(users, dict)
        and normalized_owner.casefold() not in {
            str(username).strip().casefold() for username in users
        }
    ):
        raise HTTPException(403, "API token owner is not a configured user")
    return normalized_owner


def require_api_token_scope(request: Request, required_scope: str) -> Optional[str]:
    """Require one declared scope for bearer callers; leave browser callers unchanged."""
    if not _is_api_token_request(request):
        return effective_user(request)
    state = getattr(request, "state", None)
    raw_scopes = getattr(state, "api_token_scopes", None)
    if isinstance(raw_scopes, (list, tuple, set, frozenset)):
        scopes = {
            value.strip().casefold()
            for value in raw_scopes
            if isinstance(value, str) and value.strip()
        }
    else:
        scopes = set()
    normalized_scope = str(required_scope or "").strip().casefold()
    if not normalized_scope or normalized_scope not in scopes:
        raise HTTPException(403, f"API token missing required scope: {required_scope}")
    return require_api_token_owner(request)


def require_chat_scope(request: Request) -> Optional[str]:
    """FastAPI dependency for owner-scoped chat/session routes."""
    return require_api_token_scope(request, "chat")


def require_interactive_request(request: Request) -> Optional[str]:
    """Reject bearer integrations from browser-only agent/control surfaces.

    This is deliberately a bearer-principal gate rather than an authentication
    requirement. Cookie sessions and AUTH_ENABLED=false keep their existing
    route behavior, while API tokens cannot enter routes that start, resume,
    approve, or otherwise control interactive agent work.
    """
    current_user = get_current_user(request)
    if is_bearer_principal(request):
        raise HTTPException(403, "API tokens cannot use this interactive surface")
    return current_user


def require_non_bearer_request(request: Request) -> Optional[str]:
    """Reject bearer principals while preserving cookie/local route behavior."""
    if is_bearer_principal(request):
        raise HTTPException(403, "API tokens cannot use this host-control surface")
    return get_current_user(request)


def enforce_api_token_chat_controls(
    request: Request,
    *,
    mode: str,
    plan_mode: bool,
    approval_id: object,
    allow_bash: object,
) -> bool:
    """Reject bearer-token controls that can enter or authorize agent execution."""
    is_api_token = _is_api_token_request(request)
    if is_api_token and (
        approval_id
        or plan_mode
        or mode != "chat"
        or str(allow_bash or "").lower() == "true"
    ):
        raise HTTPException(403, "API tokens cannot use agent tools or approve tool calls")
    return is_api_token


def require_authenticated_request(request: Request) -> str:
    """Allow either a browser session or a valid bearer API token.

    This is intentionally narrower than :func:`require_user`: use it only for
    routes that need authentication but do not read or mutate owner-scoped
    user data. Owner-scoped routes should use ``require_user`` for browser
    sessions or their own API-token scope/owner gate.
    """
    if is_bearer_principal(request):
        return require_api_token_owner(request)
    return require_user(request)


def _auth_disabled() -> bool:
    """True when the operator has explicitly turned off auth via .env.
    Mirrors the AUTH_ENABLED parse in app.py / core/middleware.py so the
    three call sites agree on what "off" means."""
    return auth_disabled()


def storage_owner_for_request(request: Request) -> Optional[str]:
    """Resolve the storage owner for code paths that need an owner bucket.

    This does not replace route authentication. It only gives auth-disabled
    no-login mode a stable storage identity instead of writing new data as
    legacy NULL/ownerless state.
    """
    return effective_storage_owner(effective_user(request))


def require_user(request: Request) -> str:
    """FastAPI dependency: reject unauthenticated callers when the upstream
    auth middleware was bypassed unexpectedly (e.g. SSRF from a sibling
    service). Returns the resolved username, or "" in single-user / anonymous
    modes where no username is available.

    The three "" cases are:
      1. AUTH_ENABLED=false — the operator explicitly turned auth off.
         The full /login flow is skipped (issue #622), so route-level
         require_user must let the request through too instead of 401-ing
         and forcing the browser to /login.
      2. Unconfigured first-run + loopback caller — pre-setup access from
         localhost so the operator can hit the SPA before creating the
         first admin.
      3. LOCALHOST_BYPASS=true + loopback caller — documented dev bypass.

    Use this on routes that touch user data so middleware misconfig can't
    open them up.
    """
    if is_bearer_principal(request):
        raise HTTPException(403, "API tokens must use a scope-aware API route")

    u = get_current_user(request)
    if u:
        return u
    # Operator-disabled auth: honor it at the route layer too. Without this,
    # routes that depend on require_user 401, the front-end fetch wrapper
    # redirects to /login, and the user sees a login page despite
    # AUTH_ENABLED=false (issue #622). Docker / reverse-proxy deployments
    # hit this because requests arrive from a non-loopback client.host, so
    # the loopback fall-through below never fires.
    if _auth_disabled():
        return ""
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    is_loopback = host in ("127.0.0.1", "::1", "localhost")
    # LOCALHOST_BYPASS=true is the dev-only "I'm on loopback, skip auth"
    # switch. Mirror the middleware so routes don't 401 the same caller
    # the middleware just let through.
    if is_loopback and os.getenv("LOCALHOST_BYPASS", "false").lower() == "true":
        return ""
    if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    # Unconfigured / first-run mode: only allow loopback callers.
    if is_loopback:
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
    if not isinstance(privs, dict):
        privs = {}
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
