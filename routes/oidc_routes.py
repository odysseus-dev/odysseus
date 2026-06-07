"""OpenID Connect authentication routes — login, callback, config."""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse

from core.auth import AuthManager
from core.oidc import OidcManager, OidcError

logger = logging.getLogger(__name__)

SESSION_COOKIE = "odysseus_session"


def setup_oidc_routes(
    auth_manager: AuthManager,
    oidc_manager: Optional[OidcManager],
) -> APIRouter:
    router = APIRouter(prefix="/api/auth/oidc", tags=["oidc"])

    @router.get("/config")
    async def oidc_config():
        """Return public OIDC configuration for the login page.

        Never exposes the client secret — only enough for the frontend to
        decide whether to show the OIDC login button.
        """
        from core.oidc import get_oidc_init_error
        if oidc_manager is None or not oidc_manager.configured:
            error = get_oidc_init_error()
            return {"enabled": False, "error": error or "OIDC not configured"}
        return {
            "enabled": True,
            "provider_name": oidc_manager.provider_name,
        }

    @router.get("/login")
    async def oidc_login(request: Request):
        """Initiate OIDC authorization code flow.

        Generates state + nonce, stores them server-side, then redirects
        the browser to the provider's authorization endpoint.
        """
        if oidc_manager is None or not oidc_manager.configured:
            return JSONResponse(
                {"error": "OIDC is not configured"}, status_code=503,
            )

        # Build the redirect_uri from the incoming request so it works
        # behind proxies (use the same scheme/host the browser used).
        base = str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/api/auth/oidc/callback"

        try:
            auth_url, _state, _nonce = oidc_manager.get_authorization_url(redirect_uri)
        except OidcError as exc:
            logger.error("Failed to build OIDC authorization URL: %s", exc)
            return RedirectResponse(
                url=f"/login?error=oidc_config", status_code=302,
            )

        return RedirectResponse(url=auth_url, status_code=302)

    @router.get("/callback")
    async def oidc_callback(request: Request, response: Response):
        """Handle the OIDC provider's redirect after authentication.

        Verifies state, exchanges the authorization code for tokens,
        validates the id_token, then creates (or looks up) the local
        user account and sets a session cookie.
        """
        if oidc_manager is None or not oidc_manager.configured:
            return JSONResponse(
                {"error": "OIDC is not configured"}, status_code=503,
            )

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")
        error_description = request.query_params.get("error_description", "")

        if error:
            logger.warning("OIDC provider returned error: %s — %s", error, error_description)
            return RedirectResponse(
                url=f"/login?error=oidc_denied", status_code=302,
            )

        if not code or not state:
            logger.warning("OIDC callback missing code or state")
            return RedirectResponse(
                url=f"/login?error=oidc_invalid", status_code=302,
            )

        # Build redirect_uri matching the one used in /login
        base = str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/api/auth/oidc/callback"

        # The nonce was stored server-side alongside the state in
        # OidcManager.get_authorization_url.  exchange_code pops the
        # state entry and recovers the nonce internally.
        try:
            claims = oidc_manager.exchange_code(code, state, redirect_uri)
        except OidcError as exc:
            logger.error("OIDC code exchange failed: %s", exc)
            return RedirectResponse(
                url=f"/login?error=oidc_failed", status_code=302,
            )

        # Extract identity claims
        sub = claims.get("sub", "")
        issuer = oidc_manager.issuer
        email = claims.get("email", "")
        preferred_username = claims.get("preferred_username", "")
        name = claims.get("name", "")
        groups = claims.get("groups", [])

        if not sub:
            logger.error("OIDC id_token missing sub claim")
            return RedirectResponse(
                url=f"/login?error=oidc_failed", status_code=302,
            )

        # Determine admin status from IdP group membership.
        # OIDC_ADMIN_GROUPS is a comma-separated list; the user gets
        # admin if their `groups` claim intersects with it.
        admin_group_list = [
            g.strip() for g in os.getenv("OIDC_ADMIN_GROUPS", "").split(",") if g.strip()
        ]
        is_admin = False
        if admin_group_list and groups:
            if isinstance(groups, list):
                group_set = {str(g) for g in groups}
                is_admin = bool(group_set & set(admin_group_list))

        # Determine username: use preferred_username first, then email
        # local-part, then the sub as a last resort.
        raw_username = ""
        if preferred_username:
            raw_username = preferred_username
        elif email:
            raw_username = email.split("@")[0]
        elif name:
            raw_username = name
        else:
            raw_username = sub[:32]

        raw_username = raw_username.strip().lower()
        if not raw_username:
            raw_username = f"oidc_{sub[:16]}"

        # Look up or create the user
        username = auth_manager.get_user_by_oidc(sub, issuer)
        if username is not None:
            # Existing OIDC user — sync admin status from IdP groups
            logger.info("OIDC login for existing user '%s'", username)
            auth_manager.set_oidc_user_admin(username, is_admin)
        else:
            username = auth_manager.create_user_oidc(
                raw_username, sub, issuer, email=email, is_admin=is_admin,
            )
            if username is None:
                logger.error("Failed to create OIDC user for sub=%s", sub)
                return RedirectResponse(
                    url=f"/login?error=oidc_failed", status_code=302,
                )

        # Issue a session cookie (same as password login)
        import asyncio
        token = await asyncio.to_thread(auth_manager.create_session_trusted, username)

        cookie_kwargs = dict(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            secure=os.getenv("SECURE_COOKIES", "false").lower() == "true",
            path="/",
            max_age=60 * 60 * 24 * 7,  # 7 days
        )
        response.set_cookie(**cookie_kwargs)
        response.status_code = 302
        response.headers["location"] = "/"
        return response

    return router
