"""OpenID Connect authentication routes — login, callback, config."""

import asyncio
import functools
import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse

from core.auth import AuthManager
from core.oidc import OidcManager, OidcError

logger = logging.getLogger(__name__)

SESSION_COOKIE = "odysseus_session"
OIDC_CSRF_COOKIE = "odysseus_oidc_csrf"
OIDC_CSRF_MAX_AGE = 600  # 10 minutes, matches state TTL


def _admin_group_list() -> list:
    """Return the parsed OIDC_ADMIN_GROUPS list, or empty."""
    return [
        g.strip()
        for g in os.getenv("OIDC_ADMIN_GROUPS", "").split(",")
        if g.strip()
    ]


def setup_oidc_routes(
    auth_manager: AuthManager,
    oidc_manager: Optional[OidcManager],
) -> APIRouter:
    router = APIRouter(prefix="/api/auth/oidc", tags=["oidc"])

    def _build_oidc_error_redirect(error_code: str, state: str | None = None) -> RedirectResponse:
        """Build a RedirectResponse that both redirects to /login with
        an error and clears the OIDC CSRF cookie."""
        from urllib.parse import urlencode
        params = {"error": error_code}
        if state:
            params["state"] = state
        qs = urlencode(params)
        response = RedirectResponse(url=f"/login?{qs}", status_code=302)
        response.delete_cookie(
            key=OIDC_CSRF_COOKIE, path="/api/auth/oidc/callback",
        )
        return response

    @router.get("/config")
    async def oidc_config():
        """Return public OIDC configuration for the login page.

        Never exposes the client secret — only enough for the frontend to
        decide whether to show the OIDC login button.
        """
        from core.oidc import get_oidc_init_error
        if oidc_manager is None or not oidc_manager.configured:
            init_error = get_oidc_init_error()
            if init_error:
                logger.warning("OIDC config endpoint: %s", init_error)
            return {"enabled": False, "error": "OIDC not configured"}
        return {
            "enabled": True,
            "provider_name": oidc_manager.provider_name,
        }

    @router.get("/login")
    async def oidc_login(request: Request):
        """Initiate OIDC authorization code flow.

        Sets an HttpOnly CSRF cookie bound to the state token so the
        callback can verify the same browser that started the flow is
        the one completing it (login CSRF protection).
        """
        if oidc_manager is None or not oidc_manager.configured:
            return JSONResponse(
                {"error": "OIDC is not configured"}, status_code=503,
            )

        # Use OIDC_REDIRECT_URI when explicitly configured (proxy-safe).
        # Otherwise derive from the inbound request — acceptable for
        # single-host deployments but depends on accurate Host header
        # behind proxies.
        redirect_uri = oidc_manager.redirect_uri_override
        if not redirect_uri:
            base = str(request.base_url).rstrip("/")
            redirect_uri = f"{base}/api/auth/oidc/callback"

        try:
            auth_url, _state, _ = oidc_manager.get_authorization_url(redirect_uri)
        except OidcError as exc:
            logger.error("Failed to build OIDC authorization URL: %s", exc)
            return RedirectResponse(
                url=f"/login?error=oidc_config", status_code=302,
            )

        # Set a CSRF cookie binding the state to this browser. The
        # callback verifies state == csrf_cookie before proceeding.
        response = RedirectResponse(url=auth_url, status_code=302)
        response.set_cookie(
            key=OIDC_CSRF_COOKIE,
            value=_state,
            httponly=True,
            samesite="lax",
            secure=_oidc_cookie_secure(),
            path="/api/auth/oidc/callback",
            max_age=OIDC_CSRF_MAX_AGE,
        )
        return response

    @router.get("/callback")
    async def oidc_callback(request: Request, response: Response):
        """Handle the OIDC provider's redirect after authentication.

        Verifies CSRF state cookie, exchanges the authorization code for
        tokens, validates the id_token, then creates (or looks up) the
        local user account and sets a session cookie.
        """
        if oidc_manager is None or not oidc_manager.configured:
            resp = JSONResponse({"error": "OIDC is not configured"}, status_code=503)
            resp.delete_cookie(key=OIDC_CSRF_COOKIE, path="/api/auth/oidc/callback")
            return resp

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")
        error_description = request.query_params.get("error_description", "")

        if error:
            logger.warning("OIDC provider returned error: %s — %s", error, error_description)
            return _build_oidc_error_redirect("oidc_denied", state=state)

        if not code or not state:
            logger.warning("OIDC callback missing code or state")
            return _build_oidc_error_redirect("oidc_invalid")

        # Verify the CSRF cookie matches the state parameter — ensures
        # the browser completing the flow is the same one that started it.
        csrf_cookie = request.cookies.get(OIDC_CSRF_COOKIE, "")
        if not csrf_cookie or not secrets.compare_digest(csrf_cookie, state):
            logger.warning("OIDC CSRF cookie mismatch")
            return _build_oidc_error_redirect("oidc_csrf")

        # Use OIDC_REDIRECT_URI when explicitly configured (proxy-safe),
        # matching the value used in /login.
        redirect_uri = oidc_manager.redirect_uri_override
        if not redirect_uri:
            base = str(request.base_url).rstrip("/")
            redirect_uri = f"{base}/api/auth/oidc/callback"

        try:
            claims = await asyncio.to_thread(oidc_manager.exchange_code, code, state, redirect_uri)
        except OidcError as exc:
            logger.error("OIDC code exchange failed: %s", exc)
            return _build_oidc_error_redirect("oidc_failed")

        # Extract identity claims
        sub = claims.get("sub", "")
        issuer = oidc_manager.issuer
        email = claims.get("email", "")
        preferred_username = claims.get("preferred_username", "")
        name = claims.get("name", "")
        groups_claim_present = "groups" in claims
        groups = claims.get("groups", [])
        # Authoritative group evidence = a well-formed (list) groups claim
        # from the verified id_token or the sub-bound UserInfo response.
        # A missing or malformed claim is not evidence of membership loss.
        groups_evidence_valid = groups_claim_present and isinstance(groups, list)

        # Validate claim types before use — malformed IdP claims must not
        # cause 500s or unsafe persistence operations.
        # The sub is an opaque identifier (OIDC Core §8): it is validated
        # for type and bounds but never normalized — trimming whitespace
        # could collapse two distinct verified subjects into one local
        # account and hand one subject the other's session and privileges.
        if not isinstance(sub, str) or not sub:
            logger.error("OIDC id_token sub is not a non-empty string: %r", sub)
            return _build_oidc_error_redirect("oidc_failed")
        if len(sub) > 512:
            logger.error("OIDC id_token sub exceeds maximum length")
            return _build_oidc_error_redirect("oidc_failed")
        if not isinstance(email, str) or len(email) > 512:
            email = ""
        if not isinstance(preferred_username, str) or len(preferred_username) > 256:
            preferred_username = ""
        if not isinstance(name, str) or len(name) > 512:
            name = ""
        if not isinstance(groups, list):
            groups = []
        else:
            clean_groups = []
            for group in groups:
                if isinstance(group, str):
                    if len(group) <= 256:
                        clean_groups.append(group)
                    else:
                        logger.warning("OIDC groups claim element exceeds maximum length")
                else:
                    logger.warning(
                        "OIDC groups claim contained non-string element %r — coerced",
                        group,
                    )
                    normalized_group = str(group)
                    if len(normalized_group) <= 256:
                        clean_groups.append(normalized_group)
            groups = clean_groups

        userinfo_available = claims.pop("_userinfo_available", False)

        # Determine admin status from IdP group membership.
        # OIDC_ADMIN_GROUPS is a comma-separated list; the user gets
        # admin if their `groups` claim intersects with it.
        admin_groups = _admin_group_list()
        is_admin = False
        if admin_groups and groups:
            if isinstance(groups, list):
                group_set = {str(g) for g in groups}
                is_admin = bool(group_set & set(admin_groups))

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
            # Existing OIDC user — only sync admin status from IdP groups
            # when group-based admin management is actually configured.
            # Otherwise the bootstrap (or manual grant) would be undone
            # on the next login.
            if admin_groups:
                # Only sync admin status when a trusted source (verified
                # id_token or sub-bound UserInfo) supplied a well-formed
                # groups claim.  A missing or malformed claim — e.g. a
                # transient provider failure or a scope/configuration
                # change — must not silently demote an existing admin.
                if groups_evidence_valid:
                    logger.info("OIDC login for existing user '%s' (admin=%s)", username, is_admin)
                    auth_manager.set_oidc_user_admin(username, is_admin)
                else:
                    logger.info(
                        "OIDC login for existing user '%s' — skipping admin sync "
                        "(no valid groups claim; userinfo_available=%s)",
                        username,
                        userinfo_available,
                    )
            else:
                logger.info("OIDC login for existing user '%s'", username)
        else:
            username = auth_manager.create_user_oidc(
                raw_username, sub, issuer, email=email, is_admin=is_admin,
            )
            if username is None:
                logger.error("Failed to create OIDC user for sub=%s", sub)
                return _build_oidc_error_redirect("oidc_failed")

        # Defense-in-depth: refuse to issue an OIDC session for a user
        # that somehow has local TOTP enabled (externally-edited auth.json
        # or pre-OIDC legacy account).
        if await asyncio.to_thread(auth_manager.check_oidc_totp, username):
            logger.warning(
                "OIDC user '%s' has TOTP enabled — refusing session "
                "(TOTP must be managed through the IdP)",
                username,
            )
            return _build_oidc_error_redirect("oidc_failed")

        # Issue a session cookie (same as password login)
        token = await asyncio.to_thread(auth_manager.create_session_trusted, username)
        if token is None:
            logger.error("Failed to create OIDC session for '%s'", username)
            return _build_oidc_error_redirect("oidc_failed")

        cookie_kwargs = dict(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            secure=_oidc_cookie_secure(),
            path="/",
            max_age=60 * 60 * 24 * 7,  # 7 days
        )
        response.set_cookie(**cookie_kwargs)
        # Clear the CSRF cookie (single-use)
        response.delete_cookie(key=OIDC_CSRF_COOKIE, path="/api/auth/oidc/callback")
        response.status_code = 302
        response.headers["location"] = "/"
        return response

    return router


def _oidc_cookie_secure() -> bool:
    """Determine whether OIDC cookies get the Secure flag.

    OIDC cookies are Secure by default: SSO implies a real deployment
    behind TLS, and deriving the flag from SECURE_COOKIES (which the
    bundled Compose files default to false) or the request scheme (which
    is http behind a TLS-terminating proxy) would silently issue bearer
    session cookies eligible for cleartext transmission.

    The only opt-out is the explicit development override
    OIDC_ALLOW_INSECURE_COOKIES=true, for plain-HTTP local testing.
    Nothing else — not SECURE_COOKIES, not the request scheme — can
    downgrade OIDC cookies.
    """
    if os.getenv("OIDC_ALLOW_INSECURE_COOKIES", "").strip().lower() in ("true", "1", "yes"):
        _warn_insecure_cookies_once()
        return False
    return True


@functools.lru_cache(maxsize=1)
def _warn_insecure_cookies_once() -> None:
    # Once per process, not once per login — the flag doesn't change at
    # runtime and repeating the warning twice per flow is pure log spam.
    logger.warning(
        "OIDC_ALLOW_INSECURE_COOKIES=true — OIDC session and CSRF "
        "cookies are issued without the Secure flag. Never use this "
        "outside plain-HTTP local development."
    )
