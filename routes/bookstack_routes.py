"""BookStack API proxy routes.

Proxies requests to an external BookStack instance with API token authentication.
"""
from __future__ import annotations

import logging
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bookstack", tags=["bookstack"])


def _require_admin(request: Request):
    """Reject non-admin callers."""
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        return
    user = getattr(request.state, "current_user", None)
    if not user or user == "api":
        from fastapi import HTTPException
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin only")


def _get_bookstack_config():
    """Get BookStack URL and token from integrations or settings."""
    # Try integrations first (stored in data/integrations.json)
    try:
        from src.integrations import load_integrations
        integrations = load_integrations()
        for integ in integrations:
            name = (integ.get("name") or "").lower()
            preset = (integ.get("preset") or "").lower()
            if preset == "bookstack" or name == "bookstack":
                url = (integ.get("base_url") or integ.get("url") or "").rstrip("/")
                token = integ.get("api_key") or integ.get("token") or ""
                if url:
                    return url, token
    except Exception:
        pass

    # Fallback to settings
    from src.settings import load_settings
    settings = load_settings()
    url = settings.get("bookstack_url", "").rstrip("/")
    token = settings.get("bookstack_token", "")
    return url, token


def _get_headers():
    """Build headers for BookStack API requests."""
    url, token = _get_bookstack_config()
    headers = {"Accept": "application/json"}
    if token:
        # BookStack expects "Token {token_id}:{secret}" format
        headers["Authorization"] = f"Token {token}"
    return headers


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_bookstack(path: str, request: Request):
    """Proxy all /api/bookstack/* requests to the BookStack instance."""
    _require_admin(request)

    base_url, token = _get_bookstack_config()
    if not base_url:
        return Response(
            content=b'{"error": "BookStack URL not configured. Set bookstack_url in Settings."}',
            status_code=400,
            media_type="application/json",
        )

    target_url = f"{base_url}/api/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    headers = _get_headers()
    body = await request.body()

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body if body else None,
                follow_redirects=True,
            )
            excluded_headers = {
                "content-length", "transfer-encoding", "connection",
                "x-frame-options", "content-security-policy",
            }
            response_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in excluded_headers
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=response_headers,
            )
        except httpx.ConnectError:
            return Response(
                content=f'{{"error": "Cannot connect to BookStack at {base_url}"}}'.encode(),
                status_code=502,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"BookStack proxy error: {e}")
            return Response(
                content=f'{{"error": "{str(e)}"}}'.encode(),
                status_code=502,
                media_type="application/json",
            )


@router.get("/test")
async def test_connection(request: Request):
    """Test BookStack connection and return system info."""
    _require_admin(request)

    base_url, token = _get_bookstack_config()
    if not base_url:
        return {"ok": False, "error": "BookStack URL not configured"}

    if not token:
        return {"ok": False, "error": "BookStack API token not configured"}

    if ":" not in token:
        return {
            "ok": False,
            "error": "Invalid token format. Expected 'token_id:secret'"
        }
        return {
            "ok": False,
            "error": "Invalid token format. BookStack expects 'Token {token_id}:{secret}'. "
                     "You provided only part of the token. "
                     "Go to BookStack → Profile → API Tokens → Create Token, "
                     "and copy the FULL token string including the colon separator."
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/api/system",
                headers=_get_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"ok": True, "version": data.get("version", "unknown")}
            elif resp.status_code == 401:
                return {"ok": False, "error": "Authentication failed. Check your API token."}
            else:
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def setup_bookstack_routes() -> APIRouter:
    return router
