"""WebSocket routes for real-time push notifications."""

import asyncio
import logging
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from routes.auth_routes import SESSION_COOKIE

logger = logging.getLogger(__name__)


# ── In-memory notification channels ──────────────────────────────────────
# Maps owner username → list of asyncio.Queue instances, one per connected WS.
_notify_channels: Dict[str, List[asyncio.Queue]] = defaultdict(list)


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

        # ── Auth: validate session cookie ────────────────────────────────
        session_id = websocket.cookies.get(SESSION_COOKIE)
        auth_mgr = getattr(websocket.app.state, "auth_manager", None)

        # Also check bearer token in headers (used by API-token callers)
        auth_header = websocket.headers.get("authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

        owner = None
        used_credential = None  # which credential to revalidate on each send
        if auth_mgr:
            if token:
                if auth_mgr.validate_token(token):
                    owner = auth_mgr.get_username_for_token(token)
                    used_credential = token
            if not owner and session_id:
                if auth_mgr.validate_token(session_id):
                    owner = auth_mgr.get_username_for_token(session_id)
                    used_credential = session_id

        # Fallback: if auth is disabled, allow anonymous
        if not owner and not (auth_mgr and auth_mgr.is_configured):
            owner = ""

        if owner is None:
            await websocket.close(code=4001)
            return

        # ── Subscribe and stream ─────────────────────────────────────────
        q = _subscribe(owner)
        try:
            while True:
                notification = await q.get()
                # Re-validate credential on each send so revoked/deleted/renamed
                # sessions are cut off, not silently kept alive.
                if auth_mgr and used_credential and (
                    not auth_mgr.validate_token(used_credential) or
                    auth_mgr.get_username_for_token(used_credential) != owner
                ):
                    await websocket.close(code=4001)
                    return
                try:
                    await websocket.send_json(notification)
                except WebSocketDisconnect:
                    raise
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            _unsubscribe(owner, q)

    return router
