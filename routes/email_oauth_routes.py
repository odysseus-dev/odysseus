"""
email_oauth_routes.py — OAuth2 (XOAUTH2) connect flow for IMAP/SMTP accounts.

Endpoints (all under /api/email/oauth):
    GET  /providers   list provider presets (no secrets)
    POST /authorize   persist provider+client on an owned account, return auth URL
    GET  /callback    provider redirect → exchange code, store encrypted tokens

The OAuth ``state`` is HMAC-signed (account_id + owner + nonce + issued-at) with a
per-process key, so there is no server-side pending-state store and a callback
cannot be forged or replayed onto another account. State is valid for 10 minutes;
an app restart invalidates in-flight flows (the user simply reconnects).
"""

from __future__ import annotations

# P10 RELAXATIONS: R4 — setup_email_oauth_routes exceeds the 40-line target
# because it follows Odysseus's setup_*_routes router idiom (route handlers
# defined as closures over `router`); splitting them out would break the
# established pattern used by every other routes module in the project.

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from src import email_oauth
from src.secret_storage import encrypt as _encrypt, decrypt as _decrypt
from routes.email_helpers import require_owner, _assert_owns_account, _load_settings
from core.database import SessionLocal, EmailAccount

logger = logging.getLogger(__name__)

# Per-process HMAC key for OAuth state. Set once at import (a constant). Short-
# lived state (10 min) means restart-invalidation is harmless.
_STATE_KEY = os.urandom(32)
_STATE_TTL_SECONDS = 600


def _sign_state(account_id: str, owner: str) -> str:
    """Return an HMAC-signed, time-stamped state token binding the flow."""
    assert account_id, "account_id required"
    payload = json.dumps(
        {"a": account_id, "o": owner or "",
         "n": base64.urlsafe_b64encode(os.urandom(9)).decode(),
         "t": int(time.time())},
        separators=(",", ":"),
    )
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_STATE_KEY, body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def _verify_state(state: str) -> tuple[str, str]:
    """Validate signature + TTL; return (account_id, owner). Raises on tamper."""
    if not state or "." not in state:
        raise HTTPException(400, "malformed OAuth state")
    body, _, sig = state.partition(".")
    expect = hmac.new(_STATE_KEY, body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expect):
        raise HTTPException(400, "OAuth state signature mismatch")
    pad = "=" * (-len(body) % 4)
    data = json.loads(base64.urlsafe_b64decode(body + pad).decode())
    if int(time.time()) - int(data.get("t", 0)) > _STATE_TTL_SECONDS:
        raise HTTPException(400, "OAuth state expired — restart the connection")
    return data["a"], data.get("o", "")


def _redirect_uri(request: Request) -> str:
    """The callback URL registered with the provider. Prefers app_public_url."""
    base = (_load_settings().get("app_public_url") or "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/api/email/oauth/callback"


def setup_email_oauth_routes():
    router = APIRouter(prefix="/api/email/oauth", tags=["email-oauth"])

    @router.get("/providers")
    def providers():
        """Public provider catalogue for the settings UI (no secrets)."""
        return {"providers": email_oauth.list_providers()}

    @router.post("/authorize")
    def authorize(
        request: Request,
        account_id: str = Form(...),
        provider: str = Form(...),
        client_id: str = Form(...),
        client_secret: str = Form(""),
        owner: str = Depends(require_owner),
    ):
        """Save the provider + client on an owned account, return the auth URL."""
        _assert_owns_account(account_id, owner)
        preset = email_oauth.provider_preset(provider)  # validates provider
        db = SessionLocal()
        try:
            row = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
            if row is None:
                raise HTTPException(404, "account not found")
            row.oauth_provider = provider.strip().lower()
            row.oauth_client_id = client_id.strip()
            row.oauth_client_secret = _encrypt(client_secret.strip())
            # Fill transport defaults from the preset when blank (convenience).
            row.imap_host = row.imap_host or preset["imap_host"]
            row.imap_port = row.imap_port or preset["imap_port"]
            row.smtp_host = row.smtp_host or preset["smtp_host"]
            row.smtp_port = row.smtp_port or preset["smtp_port"]
            if not (row.smtp_security or ""):
                row.smtp_security = "starttls"
            db.commit()
            hint = row.imap_user or row.from_address or ""
        finally:
            db.close()
        url = email_oauth.build_authorize_url(
            provider, client_id.strip(), _redirect_uri(request),
            _sign_state(account_id, owner), login_hint=hint or None,
        )
        return {"authorize_url": url}

    @router.get("/callback")
    def callback(
        request: Request,
        code: str = Query(None),
        state: str = Query(None),
        error: str = Query(None),
    ):
        """Provider redirect target: exchange code → store encrypted tokens."""
        if error:
            return RedirectResponse(url=f"/?email_oauth=error&reason={error}", status_code=302)
        if not code or not state:
            raise HTTPException(400, "missing code or state")
        account_id, _owner = _verify_state(state)
        db = SessionLocal()
        try:
            row = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
            if row is None:
                raise HTTPException(404, "account not found")
            tokens = email_oauth.exchange_code(
                row.oauth_provider, row.oauth_client_id,
                _decrypt(row.oauth_client_secret or ""), code, _redirect_uri(request),
            )
            row.oauth_refresh_token = _encrypt(tokens["refresh_token"])
            row.oauth_access_token = _encrypt(tokens["access_token"])
            row.oauth_token_expiry = int(tokens["expires_at"])
            row.auth_type = "oauth2"  # flip only once tokens are in hand
            db.commit()
        except email_oauth.EmailOAuthError as exc:
            logger.warning(f"email oauth callback failed: {exc}")
            return RedirectResponse(url="/?email_oauth=error", status_code=302)
        finally:
            db.close()
        return RedirectResponse(url="/?email_oauth=connected", status_code=302)

    return router
