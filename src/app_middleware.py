"""
src/app_middleware.py

Application middleware registration extracted from app.py (P8-T11).

register_middleware(app) installs, IN THE SAME ORDER as the original app.py
module body, the four always-on middlewares:

  1. CORS (CORSMiddleware)
  2. Response compression (GZipMiddleware)
  3. Security headers (SecurityHeadersMiddleware)
  4. Request hard-timeout fallback (_RequestTimeoutMiddleware)

ASGI middleware ordering is significant: middleware added later wraps (executes
before) middleware added earlier. The conditional AuthMiddleware is intentionally
NOT registered here — it is auth-coupled and stays in app.py, added after the
singletons are constructed, exactly as before.
"""

import asyncio as _asyncio
import os

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse as _JSONResponse

from core.middleware import SecurityHeadersMiddleware


# If a single request takes longer than REQUEST_HARD_TIMEOUT, abort it and
# return 504 instead of holding the event loop hostage. Whitelisted paths
# (streaming, long-running shell exec, research) are exempt because they
# legitimately stay open. Without this, a single hung subprocess.run or
# missing-timeout httpx call locks up the entire server for everyone.
REQUEST_HARD_TIMEOUT = float(os.getenv("REQUEST_HARD_TIMEOUT", "45"))
_TIMEOUT_EXEMPT_PREFIXES = (
    "/api/chat",            # streaming
    "/api/shell/stream",    # SSE
    "/api/research",        # multi-minute jobs
    "/api/model/download",  # tmux setup may run pip installs
    "/api/model/probe",     # SSE; iterates models with up to 8s timeout each
    "/api/model-endpoints", # /probe sub-route also iterates models
    "/api/cookbook/setup",  # remote pacman/apt installs
    "/api/upload",          # large files
    "/api/image",           # diffusion proxies (inpaint/harmonize/upscale/etc.) — own 120s httpx timeout
)


class _RequestTimeoutMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path or ""
        if any(path.startswith(p) for p in _TIMEOUT_EXEMPT_PREFIXES):
            return await call_next(request)
        try:
            return await _asyncio.wait_for(call_next(request), timeout=REQUEST_HARD_TIMEOUT)
        except _asyncio.TimeoutError:
            return _JSONResponse(
                {"detail": f"Request exceeded {REQUEST_HARD_TIMEOUT:.0f}s timeout"},
                status_code=504,
            )


def register_middleware(app):
    """Install CORS + security-headers + request-timeout middleware on `app`,
    preserving the original registration order."""
    # ========= CORS =========
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Auth-Token",
            "X-Odysseus-Internal-Token",
            "X-Odysseus-Owner",
            "X-Requested-With",
            "X-TZ-Offset",
        ],
    )

    # ========= RESPONSE COMPRESSION (gzip) =========
    # The frontend's text assets (style.css, index.html, the JS bundles) shipped
    # uncompressed on every cold load. gzip cuts CSS/JS/HTML by ~75-85% on the wire
    # with no behavioural change. Starlette's GZipMiddleware excludes
    # `text/event-stream` by default, so the SSE streams (chat, shell, research,
    # model-probe — all served with media_type="text/event-stream") are never
    # compressed or buffered; only complete bodies over minimum_size are. The
    # security-header middleware composes cleanly on top.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    # ========= SECURITY HEADERS MIDDLEWARE =========
    app.add_middleware(SecurityHeadersMiddleware)

    # ========= REQUEST TIMEOUT (FALLBACK FOR HUNG HANDLERS) =========
    app.add_middleware(_RequestTimeoutMiddleware)
