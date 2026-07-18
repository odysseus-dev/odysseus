"""WebSocket routes for real-time push notifications."""

import asyncio
import logging
from collections import defaultdict
from typing import Dict, List

import bcrypt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.database import get_db_session, ApiToken
from routes.auth_routes import SESSION_COOKIE

logger = logging.getLogger(__name__)


# ── In-memory notification channels ──────────────────────────────────────
# Maps owner username → list of asyncio.Queue instances, one per connected WS.
_notify_channels: Dict[str | None, List[asyncio.Queue]] = defaultdict(list)

# Sentinel key for anonymous (auth-disabled) installs
_ANONYMOUS_OWNER = "_anonymous"


def push_notification(owner: str, notification: dict):
    """Push a notification to all WS subscribers for *owner*.

    Called synchronously from task_scheduler.add_notification().
    """
    queues = _notify_channels.get(owner)
    if not queues:
        return
    stale = []
    for q in queues:
        try:
            q.put_nowait(notification)
        except asyncio.QueueFull:
            stale.append(q)
    for q in stale:
        queues.remove(q)


def _subscribe(owner: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    _notify_channels[owner].append(q)
    return q


def _unsubscribe(owner: str, q: asyncio.Queue):
    queues = _notify_channels.get(owner)
    if queues and q in queues:
        queues.remove(q)


def _validate_api_token(raw_token: str) -> str | None:
    """Validate an ``ody_`` API bearer token and return the owner, or None.

    Requires the ``notifications:read`` scope.  Returns None if the token
    is invalid, inactive, missing the required scope, or the DB is
    unavailable.
    """
    if not raw_token.startswith("ody_"):
        return None
    if len(raw_token) < 12 or len(raw_token) > 100:
        return None
    match = _find_matching_token(raw_token)
    if match is None:
        return None
    row, _ = match
    scopes = [s.strip() for s in (row.scopes or "").split(",") if s.strip()]
    if "notifications:read" not in scopes:
        return None
    return row.owner


def _find_matching_token(raw_token: str) -> tuple | None:
    """Find the ApiToken row matching *raw_token* among active rows with the
    same 8-char prefix.  Returns ``(row, raw_token)`` on match or ``None``."""
    prefix = raw_token[:8]
    try:
        with get_db_session() as db:
            rows = (
                db.query(ApiToken)
                .filter(ApiToken.token_prefix == prefix, ApiToken.is_active == True)
                .all()
            )
            for row in rows:
                if bcrypt.checkpw(raw_token.encode(), row.token_hash.encode()):
                    return row, raw_token
    except Exception:
        logger.warning("API token lookup failed", exc_info=True)
    return None


def _revalidate_api_token(raw_token: str, expected_owner: str) -> bool:
    """Re-validate an API token is still active, maps to *expected_owner*,
    and still has the ``notifications:read`` scope.

    Iterates *all* prefix-matched rows (same as ``_validate_api_token``) so
    that a valid token whose 8-char prefix collides with another active token
    is not wrongly rejected on the first per-delivery check.
    """
    match = _find_matching_token(raw_token)
    if match is None:
        return False
    row, _ = match
    scopes = [s.strip() for s in (row.scopes or "").split(",") if s.strip()]
    return row.owner == expected_owner and "notifications:read" in scopes


def setup_ws_routes():
    router = APIRouter()

    @router.websocket("/ws/notifications")
    async def ws_notifications(websocket: WebSocket):
        # ── Origin check: reject cross-origin requests (CSWSH) ───────────
        # Browsers include cookies on WebSocket handshakes regardless of
        # origin, so we must validate before accept(). Non-browser clients
        # (API tokens) omit the Origin header and are allowed through.
        origin = websocket.headers.get("origin")
        if origin:
            forwarded = websocket.headers.get("x-forwarded-proto", "")
            effective_scheme = (
                forwarded if forwarded in ("http", "https")
                else ("https" if websocket.url.scheme == "wss" else "http")
            )
            expected = f"{effective_scheme}://{websocket.url.hostname}"
            if websocket.url.port:
                expected += f":{websocket.url.port}"
            if origin != expected:
                await websocket.close(code=4001)
                return

        await websocket.accept()

        # ── Auth: validate session cookie or API bearer token ────────────
        session_id = websocket.cookies.get(SESSION_COOKIE)
        auth_mgr = getattr(websocket.app.state, "auth_manager", None)

        auth_header = websocket.headers.get("authorization", "")

        owner = None
        used_credential = None  # which credential to revalidate on each send
        _is_api_token = False

        if auth_mgr:
            # --- API bearer token (ody_...) ---
            if auth_header.startswith("Bearer ody_"):
                raw_token = auth_header[7:]
                resolved = _validate_api_token(raw_token)
                if resolved is not None:
                    owner = resolved
                    used_credential = raw_token
                    _is_api_token = True
                else:
                    await websocket.close(code=4001)
                    return

            # --- Session cookie ---
            if not owner and session_id:
                if auth_mgr.validate_token(session_id):
                    owner = auth_mgr.get_username_for_token(session_id)
                    used_credential = session_id

        # Fallback: if auth is explicitly disabled at the app level, allow anonymous
        if not owner:
            _auth_disabled = not getattr(websocket.app.state, "auth_enabled", True)
            if _auth_disabled:
                owner = _ANONYMOUS_OWNER
            else:
                await websocket.close(code=4001)
                return

        # ── Subscribe and stream ─────────────────────────────────────────
        q = _subscribe(owner)
        try:
            while True:
                notification = await q.get()
                # Re-validate credential on each send so revoked/deleted/renamed
                # sessions are cut off, not silently kept alive.
                if used_credential:
                    valid = False
                    if _is_api_token:
                        valid = _revalidate_api_token(used_credential, owner)
                    else:
                        if auth_mgr and auth_mgr.validate_token(used_credential):
                            valid = auth_mgr.get_username_for_token(used_credential) == owner
                    if not valid:
                        await websocket.close(code=4001)
                        return
                try:
                    await websocket.send_json(notification)
                except WebSocketDisconnect:
                    raise
                except Exception:
                    # Any send failure means the client is gone; exit the loop
                    # so the finally block removes the subscriber queue.
                    return
        except WebSocketDisconnect:
            pass
        finally:
            _unsubscribe(owner, q)

    return router
