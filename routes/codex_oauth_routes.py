"""Routes for connecting a ChatGPT-subscription (codex) endpoint via OAuth.

Owner-scoped, self-service: any authenticated user connects *their own* ChatGPT
subscription (no admin required). The device-code flow runs server-side because
Odysseus is a remote host — a `localhost` callback can't land in the user's
browser (see `src/providers/auth/codex_oauth.py`).

Responses expose connection STATE only — never `access_token`, `refresh_token`,
`device_auth_id`, `account_id`, or full callback URLs. The user-facing
`user_code` + `verification_uri` are the only login values returned (by design).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from core.database import CodexLoginAttempt, EndpointOAuthToken, ModelEndpoint, SessionLocal
from src.auth_helpers import require_user
from src.providers.auth import codex_oauth as cx
from src.providers.auth.oauth_store import persist_tokens
# Curated model list (the codex backend has no chat-compatible /models probe);
# owned by the provider spec, applied to the endpoint on connect.
from src.providers.spec import CODEX_MODELS

logger = logging.getLogger(__name__)

PROVIDER_ID = "openai-codex"
DEFAULT_ENDPOINT_NAME = "ChatGPT (Codex)"

# Keep strong refs to in-flight poll tasks so they aren't GC'd mid-flight.
_BG_TASKS: set[asyncio.Task] = set()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _owner(request: Request) -> Optional[str]:
    """Current user as the owner value (None in single-user/first-run mode)."""
    return require_user(request) or None


# ---------------------------------------------------------------------------
# DB helpers (sync, short-lived sessions)
# ---------------------------------------------------------------------------

@dataclass
class _AttemptFields:
    attempt_id: str
    endpoint_id: Optional[str]
    owner: Optional[str]
    device_auth_id: str
    user_code: str
    interval: int
    expires_at: Optional[datetime]


def _load_attempt_fields(attempt_id: str) -> Optional[_AttemptFields]:
    db = SessionLocal()
    try:
        row = db.get(CodexLoginAttempt, attempt_id)
        if row is None:
            return None
        return _AttemptFields(
            attempt_id=row.id, endpoint_id=row.endpoint_id, owner=row.owner,
            device_auth_id=row.device_auth_id or "", user_code=row.user_code or "",
            interval=row.interval or cx.DEFAULT_POLL_INTERVAL_SECONDS,
            expires_at=row.expires_at,
        )
    finally:
        db.close()


def _attempt_status(attempt_id: str) -> Optional[str]:
    db = SessionLocal()
    try:
        row = db.get(CodexLoginAttempt, attempt_id)
        return row.status if row else None
    finally:
        db.close()


def _mark_attempt(attempt_id: str, status: str, err: Optional[cx.CodexAuthError] = None) -> None:
    db = SessionLocal()
    try:
        row = db.get(CodexLoginAttempt, attempt_id)
        if row is None:
            return
        row.status = status
        if err is not None:
            row.last_error_redacted = str(err)[:500]  # redacted by construction
        db.commit()
    finally:
        db.close()


def _purge_endpoint_rows(db, endpoint_id: str) -> None:
    """Delete an endpoint's token row and detach its attempt log rows explicitly.
    The schema's ON DELETE CASCADE / SET NULL also fire now that the engine
    enables SQLite FK enforcement, but the explicit purge keeps deletion behavior
    independent of connection-level PRAGMA state. Caller commits."""
    db.query(EndpointOAuthToken).filter(
        EndpointOAuthToken.endpoint_id == endpoint_id
    ).delete(synchronize_session=False)
    db.query(CodexLoginAttempt).filter(
        CodexLoginAttempt.endpoint_id == endpoint_id
    ).update({"endpoint_id": None}, synchronize_session=False)


def _delete_pending_endpoint(endpoint_id: Optional[str]) -> None:
    """Drop a still-disabled endpoint (and its token row) after a failed or
    cancelled login. Enabled endpoints are left alone."""
    if not endpoint_id:
        return
    db = SessionLocal()
    try:
        ep = db.get(ModelEndpoint, endpoint_id)
        if ep is not None and not ep.is_enabled:
            _purge_endpoint_rows(db, endpoint_id)
            db.delete(ep)
            db.commit()
    finally:
        db.close()


def _enable_endpoint(endpoint_id: str) -> None:
    db = SessionLocal()
    try:
        ep = db.get(ModelEndpoint, endpoint_id)
        if ep is None:
            return
        ep.is_enabled = True
        ep.cached_models = json.dumps(CODEX_MODELS)
        ep.model_type = "llm"
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Background device-code poll loop
# ---------------------------------------------------------------------------

def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _run_login_poll(attempt_id: str) -> None:
    """Poll the deviceauth token endpoint until the user signs in, expiry, or
    cancellation. On success: exchange + persist + enable. Owns its own DB
    sessions and HTTP client (the request that started it is long gone)."""
    data = _load_attempt_fields(attempt_id)
    if data is None or not data.device_auth_id:
        return
    deadline = data.expires_at or (_utcnow_naive() + timedelta(seconds=cx.DEFAULT_DEVICE_CODE_TTL_SECONDS))

    async with httpx.AsyncClient(timeout=cx._HTTP_TIMEOUT) as client:
        while _utcnow_naive() < deadline:
            if _attempt_status(attempt_id) != "pending":
                return  # cancelled or already resolved
            try:
                result = await cx.poll_device_token(data.device_auth_id, data.user_code, client=client)
            except cx.CodexAuthError as err:
                logger.warning("codex login poll failed (attempt=%s, code=%s)", attempt_id, err.code)
                _mark_attempt(attempt_id, "error", err)
                _delete_pending_endpoint(data.endpoint_id)
                return
            if result is not None:
                await _finalize_login(data, result, client)
                return
            await asyncio.sleep(data.interval)

    _mark_attempt(attempt_id, "expired")
    _delete_pending_endpoint(data.endpoint_id)


async def _finalize_login(data: _AttemptFields, result: cx.DeviceAuthResult,
                          client: httpx.AsyncClient) -> None:
    try:
        token_set = await cx.exchange_device_code(
            result.authorization_code, result.code_verifier, client=client)
    except cx.CodexAuthError as err:
        logger.warning("codex token exchange failed (attempt=%s, code=%s)", data.attempt_id, err.code)
        _mark_attempt(data.attempt_id, "error", err)
        _delete_pending_endpoint(data.endpoint_id)
        return
    if data.endpoint_id:
        persist_tokens(data.endpoint_id, token_set, owner=data.owner,
                       provider_id=PROVIDER_ID, create_if_missing=True)
        _enable_endpoint(data.endpoint_id)
    _mark_attempt(data.attempt_id, "authorized")
    logger.info("codex login authorized (attempt=%s)", data.attempt_id)


# ---------------------------------------------------------------------------
# Public serializers (state only)
# ---------------------------------------------------------------------------

def _attempt_public(row: CodexLoginAttempt, *, expire_if_stale: bool = False) -> dict:
    status = row.status
    if status == "pending" and row.expires_at is not None and _utcnow_naive() >= row.expires_at:
        status = "expired"
        if expire_if_stale:
            _mark_attempt(row.id, "expired")
            _delete_pending_endpoint(row.endpoint_id)
    return {
        "attempt_id": row.id,
        "status": status,
        "endpoint_id": row.endpoint_id,
        "user_code": row.user_code,
        "verification_uri": row.verification_uri,
        "expires_at": row.expires_at.isoformat() + "Z" if row.expires_at else None,
    }


def _require_owned(row, owner: Optional[str]):
    # 404 (not 403) so we don't leak existence of another user's row.
    if row is None or row.owner != owner:
        raise HTTPException(404, "Not found")
    return row


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def setup_codex_oauth_routes() -> APIRouter:
    router = APIRouter(prefix="/api/providers/openai-codex")

    @router.post("/connect")
    async def connect(request: Request, name: str = DEFAULT_ENDPOINT_NAME):
        """Start a device-code login. Creates a disabled pending endpoint + an
        attempt row, kicks off background polling, and returns the user-facing
        code + verification URL (never tokens)."""
        owner = _owner(request)
        try:
            start = await cx.request_device_code()
        except cx.CodexAuthError as err:
            logger.warning("codex device-code request failed (code=%s)", err.code)
            raise HTTPException(502, "Could not start ChatGPT login. Try again shortly.")

        endpoint_id = str(uuid.uuid4())[:8]
        attempt_id = uuid.uuid4().hex
        db = SessionLocal()
        try:
            db.add(ModelEndpoint(
                id=endpoint_id, name=name.strip() or DEFAULT_ENDPOINT_NAME,
                base_url=cx.BASE_URL, api_key=None, is_enabled=False,
                model_type="llm", owner=owner,
            ))
            db.add(CodexLoginAttempt(
                id=attempt_id, owner=owner, endpoint_id=endpoint_id,
                device_auth_id=start.device_auth_id, user_code=start.user_code,
                verification_uri=start.verification_uri, status="pending",
                expires_at=start.expires_at, interval=start.interval,
            ))
            db.commit()
        finally:
            db.close()

        _spawn(_run_login_poll(attempt_id))
        return {
            "attempt_id": attempt_id,
            "endpoint_id": endpoint_id,
            "user_code": start.user_code,
            "verification_uri": start.verification_uri,
            "interval": start.interval,
            "expires_at": start.expires_at.isoformat() + "Z" if start.expires_at else None,
        }

    @router.get("/connect/{attempt_id}")
    def attempt_status(attempt_id: str, request: Request):
        """Poll a login attempt's status (UI calls this on a timer)."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = _require_owned(db.get(CodexLoginAttempt, attempt_id), owner)
            return _attempt_public(row, expire_if_stale=True)
        finally:
            db.close()

    @router.post("/connect/{attempt_id}/cancel")
    def cancel(attempt_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = _require_owned(db.get(CodexLoginAttempt, attempt_id), owner)
            endpoint_id = row.endpoint_id
            if row.status == "pending":
                row.status = "cancelled"
                db.commit()
        finally:
            db.close()
        _delete_pending_endpoint(endpoint_id)
        return {"status": "cancelled"}

    @router.get("/status")
    def status(request: Request):
        """Owner-scoped connection summary: connected endpoints + pending logins.
        State only — no token values, no account id."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            tokens = db.query(EndpointOAuthToken).filter(
                EndpointOAuthToken.owner == owner,
                EndpointOAuthToken.provider_id == PROVIDER_ID,
            ).all()
            connected = []
            for t in tokens:
                ep = db.get(ModelEndpoint, t.endpoint_id)
                connected.append({
                    "endpoint_id": t.endpoint_id,
                    "name": ep.name if ep else None,
                    "enabled": bool(ep.is_enabled) if ep else False,
                    "status": t.status,
                    "expires_at": t.expires_at.isoformat() + "Z" if t.expires_at else None,
                    "last_error": t.last_error_redacted,
                })
            pending_rows = db.query(CodexLoginAttempt).filter(
                CodexLoginAttempt.owner == owner,
                CodexLoginAttempt.status == "pending",
            ).all()
            pending = [_attempt_public(r, expire_if_stale=True) for r in pending_rows]
            pending = [p for p in pending if p["status"] == "pending"]
            return {"connected": connected, "pending": pending}
        finally:
            db.close()

    @router.post("/disconnect/{endpoint_id}")
    def disconnect(endpoint_id: str, request: Request):
        """Remove a connected codex endpoint and its tokens (owner-scoped)."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            ep = _require_owned(db.get(ModelEndpoint, endpoint_id), owner)
            _purge_endpoint_rows(db, endpoint_id)
            db.delete(ep)
            db.commit()
        finally:
            db.close()
        return {"ok": True}

    return router
