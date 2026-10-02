# src/middleware.py
# Shared middleware, decorators, and request helpers

import os
import secrets
from collections.abc import Mapping

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
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


# Headers that prove a request was forwarded by a proxy/tunnel (cloudflared,
# nginx, Caddy, Tailscale Funnel, …). cloudflared connects to the app FROM
# 127.0.0.1, so without this check every tunneled request would look like
# loopback and could bypass auth.
# Any X-Forwarded-* header at all, matched by prefix rather than by name. The
# original list named four of them and a proxy terminating on loopback that
# forwards only X-Forwarded-Proto still read as a direct loopback request. An
# enumeration of spellings is the wrong shape for this: the question is whether
# something forwarded the request, and every member of that family answers it.
PROXY_FORWARDING_HEADER_PREFIXES = ("x-forwarded-",)

# Vendor headers that mean the same thing without the X-Forwarded- prefix.
PROXY_FORWARDING_HEADERS = (
    "cdn-loop",
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "fastly-client-ip",
    "forwarded",
    "true-client-ip",
    "x-client-ip",
    "x-cluster-client-ip",
    "x-real-ip",
)


def is_proxy_forwarding_header(name: str) -> bool:
    """True when ``name`` is evidence that a proxy or tunnel relayed a request."""
    lowered = name.lower()
    return lowered in PROXY_FORWARDING_HEADERS or lowered.startswith(
        PROXY_FORWARDING_HEADER_PREFIXES
    )


def is_trusted_loopback(request: Request) -> bool:
    """True ONLY for a DIRECT loopback connection with no proxy/tunnel
    forwarding headers.

    A bare ``client.host in ('127.0.0.1','::1')`` check is unsafe behind a
    Cloudflare tunnel / reverse proxy: those connect from loopback, so a remote
    visitor would otherwise inherit local trust and slip past LOCALHOST_BYPASS
    or spoof the internal-tool path. Odysseus's own in-process agent loopback
    calls carry none of these headers, so they still qualify.

    This is the single source of truth for that rule. Both halves of the
    internal-tool contract use it — ``AuthMiddleware`` when it decides whether
    to honour the token at all, and ``require_admin`` when a route is reached
    without the middleware having stamped a user — so the two cannot drift into
    disagreeing about what counts as the in-process loopback.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    if host not in ("127.0.0.1", "::1"):
        return False
    return not any(
        value for name, value in request.headers.items() if is_proxy_forwarding_header(name)
    )


def is_cors_preflight(method: str, headers) -> bool:
    """True for a genuine CORS preflight: an OPTIONS request carrying the
    Access-Control-Request-Method header. Such requests are credential-less by
    design and must reach CORSMiddleware to be answered -- gating them on auth
    401s the preflight and breaks every cross-origin browser/WebView client.
    Pure so it can be unit-tested without standing up the app."""
    return method == "OPTIONS" and "access-control-request-method" in headers


def require_admin(request: Request):
    """Raise 403 if the current user isn't an admin.

    Allows access when auth is explicitly disabled, or when the request is the
    in-process tool loopback described in THREAT_MODEL.md. That loopback is
    recognised two ways, and both are narrower than "the token is present":

    (b) ``request.state.current_user`` is the reserved ``internal-tool``
        sentinel. Only ``AuthMiddleware`` sets that, and only after checking
        the token *and* ``is_trusted_loopback``. The name lives in
        RESERVED_AUTH_USERNAMES, so no real account can produce it.

    (a) The caller set ``X-Odysseus-Internal-Token`` directly, for a route
        ``AuthMiddleware`` did not gate. This path applies the same origin rule
        the middleware applies — token *and* direct loopback — so possession of
        the per-process token is not on its own an admin credential from an
        arbitrary client address.

    Path (a) is consulted only when the request was not attributed to a real
    user. When the loopback runs on behalf of a session owner it names them in
    ``X-Odysseus-Owner`` and ``AuthMiddleware`` puts that username in
    ``request.state.current_user``; that user's own admin status then decides.
    Otherwise a tool call made for a NON-admin owner would be handed admin by
    the shared per-process token — precisely the escalation the owner
    attribution exists to prevent.
    """
    try:
        state_user = getattr(request.state, "current_user", None)
    except Exception:
        state_user = None

    # (b) Already validated by AuthMiddleware.
    if state_user == INTERNAL_TOOL_USER:
        return

    # (a) Header-direct, ungated route, no owner attributed to the request.
    if state_user is None:
        try:
            hdr = request.headers.get(INTERNAL_TOOL_HEADER)
            if (
                hdr
                and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN)
                and is_trusted_loopback(request)
            ):
                return
        except Exception:
            pass

    if auth_disabled():
        return
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if not auth_mgr or not auth_mgr.is_configured:
        raise HTTPException(403, "Admin only")
    if not state_user or not auth_mgr.is_admin(state_user):
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
