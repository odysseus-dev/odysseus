# src/middleware.py
# Shared middleware, decorators, and request helpers

import os
import secrets
from collections.abc import Mapping

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import get_route_path

from src.owner_identity import INTERNAL_TOOL_USER, auth_disabled


# Per-process token that lets the in-app tool layer hit admin-gated
# routes via HTTP loopback (the agent's tool calls don't carry the
# admin user's session cookie). Set once at import; tools read the
# same value from this module. Never persisted or exposed externally.
INTERNAL_TOOL_TOKEN = os.environ.get("ODYSSEUS_INTERNAL_TOKEN") or secrets.token_hex(32)
INTERNAL_TOOL_HEADER = "X-Odysseus-Internal-Token"


def get_application_route_path(scope: Mapping[str, object]) -> str:
    """Return the application-relative path used by Starlette routing.

    Uvicorn prefixes ``scope["path"]`` with a configured ASGI ``root_path``;
    Starlette removes that prefix before matching routes. Middleware policy
    must use the same path form or a deployment prefix can change which policy
    applies to an otherwise unchanged application route.
    """
    return get_route_path(scope)


def with_asgi_root_path(scope: Mapping[str, object], path: str) -> str:
    """Prefix an application path for a client-facing redirect target."""
    root_path = scope.get("root_path", "")
    if not isinstance(root_path, str) or not root_path:
        return path
    return f"{root_path.rstrip('/')}{path}"


def path_is_route_or_child(path: str, prefix: str) -> bool:
    """Return whether ``path`` is exactly ``prefix`` or below that route."""
    return path == prefix or path.startswith(prefix + "/")


CODEX_COOKBOOK_PREFIX = "/api/codex/cookbook"


def is_cors_preflight(method: str, headers) -> bool:
    """True for a genuine CORS preflight: an OPTIONS request carrying the
    Access-Control-Request-Method header. Such requests are credential-less by
    design and must reach CORSMiddleware to be answered -- gating them on auth
    401s the preflight and breaks every cross-origin browser/WebView client.
    Pure so it can be unit-tested without standing up the app."""
    return method == "OPTIONS" and "access-control-request-method" in headers


def is_codex_cookbook_path(path: str) -> bool:
    """Match only the duplicate Codex Cookbook route family."""
    return path == CODEX_COOKBOOK_PREFIX or path.startswith(
        f"{CODEX_COOKBOOK_PREFIX}/"
    )


def is_odysseus_bearer_authorization(value: str | None) -> bool:
    """Recognize an Odysseus Bearer value, including proxy-combined fields."""
    if not isinstance(value, str):
        return False
    for candidate in value.split(","):
        parts = candidate.strip().split(None, 1)
        if (
            len(parts) == 2
            and parts[0].casefold() == "bearer"
            and parts[1].startswith("ody_")
        ):
            return True
    return False


def _header_values(headers, name: str) -> list[str]:
    """Return every field value, with a mapping fallback for direct callers."""
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        values = getlist(name)
    else:
        value = headers.get(name)
        values = value if isinstance(value, (list, tuple)) else [value]
    return [value for value in values if isinstance(value, str)]


def _internal_header_matches(value: str) -> bool:
    """Compare raw or proxy-combined values without obs-text type failures."""
    candidates = [value]
    trimmed_value = value.strip(" \t")
    if trimmed_value != value:
        candidates.append(trimmed_value)
    if "," in value:
        for part in value.split(","):
            candidates.append(part)
            trimmed_part = part.strip(" \t")
            if trimmed_part != part:
                candidates.append(trimmed_part)
    try:
        expected = INTERNAL_TOOL_TOKEN.encode("utf-8")
    except (AttributeError, UnicodeError):
        return False
    for candidate in candidates:
        try:
            if secrets.compare_digest(candidate.encode("utf-8"), expected):
                return True
        except (AttributeError, TypeError, UnicodeError):
            continue
    return False


def require_codex_cookbook_browser(request: Request) -> None:
    """Reject bearer and internal-tool principals at the shared boundary."""
    current_user = getattr(request.state, "current_user", None)
    if (
        getattr(request.state, "api_token", False)
        or current_user == "api"
        or current_user == INTERNAL_TOOL_USER
    ):
        raise HTTPException(403, "Forbidden")
    if any(
        is_odysseus_bearer_authorization(value)
        for value in _header_values(request.headers, "authorization")
    ):
        raise HTTPException(403, "Forbidden")
    if any(
        _internal_header_matches(value)
        for value in _header_values(request.headers, INTERNAL_TOOL_HEADER)
    ):
        raise HTTPException(403, "Forbidden")


class CodexCookbookBoundaryMiddleware(BaseHTTPMiddleware):
    """Apply the Codex Cookbook principal gate before request-body parsing."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if is_codex_cookbook_path(get_route_path(request.scope)):
            try:
                require_codex_cookbook_browser(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )
        return await call_next(request)


def require_admin(request: Request):
    """Raise 403 if the current user isn't an admin.
    Allows access when auth is explicitly disabled, or when the request carries
    the in-process internal-tool token used by loopback agent tools.
    """
    # In-process bypass for tool-layer loopback calls. Two paths:
    # (a) header-direct (caller set X-Odysseus-Internal-Token), or
    # (b) the auth middleware already validated the token and stamped
    #     request.state.current_user = "internal-tool".
    try:
        hdr = request.headers.get(INTERNAL_TOOL_HEADER)
        if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
            return
        if getattr(request.state, "current_user", None) == INTERNAL_TOOL_USER:
            return
    except Exception:
        pass

    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_disabled():
        return
    if not auth_mgr or not auth_mgr.is_configured:
        raise HTTPException(403, "Admin only")
    user = getattr(request.state, "current_user", None)
    if not user or not auth_mgr.is_admin(user):
        raise HTTPException(403, "Admin only")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate a per-request nonce for inline scripts
        nonce = secrets.token_hex(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)
        path = request.url.path

        # Tool render endpoints
        is_tool_render = path.startswith("/api/tools/") and path.endswith("/render")
        # Document library PDF preview endpoint
        is_document_pdf_preview = path.startswith("/api/document/") and path.endswith("/render-pdf")
        # Visual report pages are self-contained HTML — need inline scripts + external images
        is_report = path.startswith("/api/research/report/")

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"

        is_https = (
            request.url.scheme == "https"
            or request.headers.get("X-Forwarded-Proto") == "https"
        )
        if is_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        if is_report:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; "
                "img-src 'self' data: blob: https:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        elif is_tool_render:
            # Skip framing headers for tools.
            pass
        elif is_document_pdf_preview:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "frame-ancestors 'self'"
            )
        else:
            response.headers["X-Frame-Options"] = "DENY"
            # NOTE: `style-src 'unsafe-inline'` is intentionally retained.
            # `static/index.html` and `static/login.html` ship inline <style>
            # blocks, and several JS modules build runtime `style=""` attrs.
            # Migrating to nonce-only requires templating the HTML files +
            # auditing every JS-set style attribute. Since inline styles
            # don't execute script, the residual risk is visual-only.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "font-src 'self' https://cdn.jsdelivr.net; "
                "img-src 'self' data: blob: https:; "
                "media-src 'self' blob:; "
                "connect-src 'self'; "
                "frame-src 'self'; "
                "frame-ancestors 'none'"
            )
        return response
