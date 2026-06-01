"""OAuth token store + per-call credential resolution (codex / ChatGPT-sub).

This is the DB-coupled half of the auth seam: `credentials.resolve()` dispatches
here for `auth_type == "oauth"`. Responsibilities:

  * load the encrypted token row for an endpoint (by `endpoint_id`),
  * refresh the access token on expiry (single-flight per endpoint, 120s skew),
  * persist a rotated token pair,
  * build the credential-derived request headers (`Authorization` +
    `chatgpt-account-id`).

The provider-constant backend-compat headers (`originator`, `User-Agent`) are
NOT here — they are not credential-derived, so they belong to the transport's
`build_headers`, keeping the auth/transport package boundary intact.

Never log token material; `CodexAuthError` messages are already redacted.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from src.providers.auth import codex_oauth as cx
from src.providers.auth.credentials import Credential
from src.providers.endpoint_ref import EndpointRef
from src.providers.spec import ProviderSpec

logger = logging.getLogger(__name__)

# One refresh in flight per endpoint; concurrent resolves for the same endpoint
# wait, re-read, and reuse the freshly-rotated token instead of each refreshing.
_refresh_locks: dict[str, asyncio.Lock] = {}


def _lock_for(endpoint_id: str) -> asyncio.Lock:
    # setdefault-style; no await between get and set, so this is race-free on
    # a single event loop.
    lock = _refresh_locks.get(endpoint_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[endpoint_id] = lock
    return lock


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Sync DB helpers (fast, indexed; each opens its own short-lived session)
# ---------------------------------------------------------------------------

def _load_row(endpoint_id: str):
    from core.database import SessionLocal, EndpointOAuthToken
    db = SessionLocal()
    try:
        return db.query(EndpointOAuthToken).filter(
            EndpointOAuthToken.endpoint_id == endpoint_id
        ).first()
    finally:
        db.close()


def persist_tokens(
    endpoint_id: str,
    token_set: cx.TokenSet,
    *,
    owner: Optional[str] = None,
    provider_id: str = "openai-codex",
    create_if_missing: bool = False,
) -> None:
    """Write a freshly-acquired/rotated token pair to the endpoint's row.

    Used both by `resolve()` after a refresh and by the connect route after a
    successful device-code login (`create_if_missing=True`). Marks the row
    `active` and clears any prior error.
    """
    import uuid
    from core.database import SessionLocal, EndpointOAuthToken
    db = SessionLocal()
    try:
        row = db.query(EndpointOAuthToken).filter(
            EndpointOAuthToken.endpoint_id == endpoint_id
        ).first()
        if row is None:
            if not create_if_missing:
                raise HTTPException(404, "No OAuth token row for this endpoint.")
            row = EndpointOAuthToken(id=uuid.uuid4().hex, endpoint_id=endpoint_id,
                                     owner=owner, provider_id=provider_id)
            db.add(row)
        row.access_token = token_set.access_token
        row.refresh_token = token_set.refresh_token
        if token_set.account_id:
            row.account_id = token_set.account_id
        row.expires_at = token_set.expires_at
        row.last_refresh_at = _utcnow_naive()
        row.status = "active"
        row.last_error_redacted = None
        db.commit()
    finally:
        db.close()


def _record_error(endpoint_id: str, err: cx.CodexAuthError) -> None:
    from core.database import SessionLocal, EndpointOAuthToken
    db = SessionLocal()
    try:
        row = db.query(EndpointOAuthToken).filter(
            EndpointOAuthToken.endpoint_id == endpoint_id
        ).first()
        if row is None:
            return
        if err.relogin_required:
            row.status = "needs_relogin"
        elif err.code == "quota":
            row.status = "quota"
        else:
            row.status = "error"
        row.last_error_redacted = str(err)[:500]  # redacted by construction
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _needs_refresh(row) -> bool:
    if not row.access_token:
        return True
    if row.expires_at is not None:
        return row.expires_at <= _utcnow_naive() + timedelta(seconds=cx.REFRESH_SKEW_SECONDS)
    # Opaque/unknown expiry — fall back to decoding the JWT directly.
    return cx.access_token_is_expiring(row.access_token)


def _build_credential(access_token: str, account_id: Optional[str]) -> Credential:
    headers = {"Authorization": f"Bearer {access_token}"}
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return Credential(headers=headers)


def _http_from(err: cx.CodexAuthError) -> HTTPException:
    if err.relogin_required:
        return HTTPException(401, "Codex authentication expired — reconnect this endpoint.")
    if err.code == "quota":
        return HTTPException(429, "Codex usage quota reached — retry after the limit resets.")
    return HTTPException(502, "Codex token refresh failed.")


async def resolve_oauth_credential(ref: EndpointRef, spec: Optional[ProviderSpec]) -> Credential:
    """Return a live bearer credential for an OAuth endpoint, refreshing on
    expiry. `credentials._guard_oauth` has already ensured `ref.endpoint_id`."""
    endpoint_id = ref.endpoint_id
    assert endpoint_id  # guaranteed by the guard upstream

    row = _load_row(endpoint_id)
    if row is None:
        raise HTTPException(401, "Codex endpoint is not connected — connect it first.")
    if row.status == "needs_relogin":
        raise HTTPException(401, "Codex authentication expired — reconnect this endpoint.")

    if not _needs_refresh(row):
        return _build_credential(row.access_token, row.account_id)

    # Refresh path — single-flight per endpoint.
    async with _lock_for(endpoint_id):
        row = _load_row(endpoint_id)            # re-read: another coroutine may have rotated
        if row is None:
            raise HTTPException(401, "Codex endpoint is not connected — connect it first.")
        if row.status == "needs_relogin":
            raise HTTPException(401, "Codex authentication expired — reconnect this endpoint.")
        if not _needs_refresh(row):
            return _build_credential(row.access_token, row.account_id)
        if not row.refresh_token:
            raise HTTPException(401, "Codex endpoint has no refresh token — reconnect it.")

        try:
            token_set = await cx.refresh_tokens(row.refresh_token)
        except cx.CodexAuthError as err:
            logger.warning("codex refresh failed for endpoint (code=%s, relogin=%s)",
                           err.code, err.relogin_required)
            _record_error(endpoint_id, err)
            raise _http_from(err)

        persist_tokens(endpoint_id, token_set)
        account_id = token_set.account_id or row.account_id
        return _build_credential(token_set.access_token, account_id)
