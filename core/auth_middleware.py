import os
import asyncio
import logging
from datetime import datetime

import bcrypt as _bcrypt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from core.auth import AuthManager
from core.database import SessionLocal, ApiToken
from routes.auth_routes import SESSION_COOKIE

logger = logging.getLogger(__name__)

AUTH_EXEMPT_EXACT = {
    "/api/auth/setup",
    "/api/auth/signup",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/features",
    "/api/auth/settings",
    "/api/auth/integrations/presets",
    "/api/health",
    "/api/version",
    "/login",
}
AUTH_EXEMPT_PREFIXES = ["/static"]


def _is_auth_exempt(path: str) -> bool:
    return path in AUTH_EXEMPT_EXACT or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES)


def setup_auth(app):
    auth_manager = AuthManager()
    app.state.auth_manager = auth_manager
    auth_enabled = os.getenv("AUTH_ENABLED", "true").lower() != "false"
    localhost_bypass = os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"

    if not auth_enabled:
        logger.info("Auth middleware disabled (set AUTH_ENABLED=true to enable)")
        return

    _token_cache: dict = {}
    _token_cache_lock = asyncio.Lock()

    def _token_cache_invalidate():
        app.state._token_cache_dirty = True
    app.state.invalidate_token_cache = _token_cache_invalidate
    app.state._token_cache = _token_cache
    app.state._token_cache_dirty = True

    def _refresh_token_cache():
        from collections import defaultdict
        new_map = defaultdict(list)
        db = SessionLocal()
        try:
            rows = db.query(ApiToken).filter(ApiToken.is_active == True).all()
            for r in rows:
                scopes = [s.strip() for s in (getattr(r, "scopes", "") or "chat").split(",") if s.strip()]
                new_map[r.token_prefix].append((r.id, r.token_hash, getattr(r, "owner", None), scopes))
        finally:
            db.close()
        _token_cache.clear()
        _token_cache.update(new_map)
        app.state._token_cache_dirty = False

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if _is_auth_exempt(path):
                return await call_next(request)
            try:
                from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN as _ITT
                _hdr = request.headers.get(INTERNAL_TOOL_HEADER)
                _client_host = request.client.host if request.client else None
                if _hdr and _hdr == _ITT and _client_host in ("127.0.0.1", "::1"):
                    _impersonate = (request.headers.get("X-Odysseus-Owner") or "").strip()
                    request.state.current_user = _impersonate or "internal-tool"
                    request.state.api_token = False
                    return await call_next(request)
            except Exception:
                pass
            if localhost_bypass:
                client_host = request.client.host if request.client else None
                if client_host in ("127.0.0.1", "::1"):
                    return await call_next(request)
            if not auth_manager.is_configured:
                if not path.startswith("/api/"):
                    return RedirectResponse(url="/login", status_code=302)
                return JSONResponse(status_code=401, content={"error": "Setup required"})

            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer ody_"):
                raw_token = auth_header[7:]
                if len(raw_token) < 12 or len(raw_token) > 100:
                    return JSONResponse(status_code=401, content={"error": "Invalid API token"})
                prefix = raw_token[:8]
                try:
                    if app.state._token_cache_dirty:
                        async with _token_cache_lock:
                            if app.state._token_cache_dirty:
                                await asyncio.to_thread(_refresh_token_cache)
                    candidates = list(_token_cache.get(prefix, ()))
                    matched_id = None
                    matched_owner = None
                    matched_scopes = []
                    for tid, thash, owner, scopes in candidates:
                        if _bcrypt.checkpw(raw_token.encode(), thash.encode()):
                            matched_id = tid
                            matched_owner = owner
                            matched_scopes = scopes or []
                            break
                    if matched_id:
                        async def _touch_last_used(tid: str):
                            def _do():
                                _db = SessionLocal()
                                try:
                                    _db.query(ApiToken).filter(ApiToken.id == tid).update(
                                        {"last_used_at": datetime.utcnow()}
                                    )
                                    _db.commit()
                                finally:
                                    _db.close()
                            try:
                                await asyncio.to_thread(_do)
                            except Exception:
                                pass
                        asyncio.create_task(_touch_last_used(matched_id))
                        request.state.current_user = "api"
                        request.state.api_token = True
                        request.state.api_token_id = matched_id
                        request.state.api_token_owner = matched_owner
                        request.state.api_token_scopes = matched_scopes
                        return await call_next(request)
                except Exception:
                    logger.warning("API token auth error", exc_info=False)
                return JSONResponse(status_code=401, content={"error": "Invalid API token"})

            token = request.cookies.get(SESSION_COOKIE)
            if not auth_manager.validate_token(token):
                if path.startswith("/api/"):
                    return JSONResponse(status_code=401, content={"error": "Not authenticated"})
                return RedirectResponse(url="/login", status_code=302)

            request.state.current_user = auth_manager.get_username_for_token(token)
            request.state.api_token = False
            return await call_next(request)

    app.add_middleware(AuthMiddleware)
    logger.info("Auth middleware enabled (AUTH_ENABLED=true)")
