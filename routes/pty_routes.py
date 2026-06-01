"""WebSocket PTY routes — real interactive terminal over a pseudo-console.

Backs an xterm.js terminal in the browser with a true PTY (ConPTY on Windows
via pywinpty, openpty on POSIX). This makes progress bars (tqdm) and
curses/ANSI apps render correctly — impossible with the one-shot
exec + SSE-log-tail path in shell_routes.py.

SECURITY: this is an admin-only interactive shell == RCE if the gate is wrong.
Starlette's BaseHTTPMiddleware does NOT run on WebSockets, so we replicate the
exact auth flow from app.py's AuthMiddleware here, by hand, and fail CLOSED.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.pty import PtySession, PTY_BACKEND, pty_supported

logger = logging.getLogger(__name__)

# Mirror app.py's env reads (app.py:109-110) exactly.
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
LOCALHOST_BYPASS = os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"

# Same cookie name the login route sets (routes/auth_routes.py:64).
SESSION_COOKIE = "odysseus_session"

# Batch terminal output ~1 frame (16ms) to balance latency vs message volume,
# matching LiteSuite's PTY broadcast cadence.
_FLUSH_INTERVAL = 0.016


def _resolve_ws_user(websocket: WebSocket) -> tuple[bool, str | None]:
    """Replicate AuthMiddleware (app.py:163-266) for a WebSocket handshake.

    Returns (allowed, username). `allowed` False means close 4403.
    Fails CLOSED on any error.
    """
    try:
        # 1. Auth globally disabled -> middleware isn't even installed.
        if not AUTH_ENABLED:
            return True, "admin"

        auth_manager = getattr(websocket.app.state, "auth_manager", None)
        if auth_manager is None:
            # No auth subsystem at all -> dev mode, allow (mirrors shell_routes
            # _require_admin's no-auth-manager branch).
            return True, "admin"

        client_host = websocket.client.host if websocket.client else None
        is_loopback = client_host in ("127.0.0.1", "::1")

        # 2. Localhost bypass (app.py:190-194) — loopback clients skip auth.
        if LOCALHOST_BYPASS and is_loopback:
            return True, "localhost"

        # 3. Not configured yet (no users) -> reject; setup must happen first.
        if not auth_manager.is_configured:
            return False, None

        # 4. Bearer API token (Authorization: Bearer ody_...) — accept a valid
        #    token but still require it to be admin-scoped below. We only have
        #    the cookie/header here; API-token validation lives in the HTTP
        #    middleware's in-memory cache, so for the terminal we require a
        #    real admin SESSION cookie (the common, correct case) and treat
        #    bearer-only as unauthenticated for this sensitive endpoint.
        token = websocket.cookies.get(SESSION_COOKIE)
        if not auth_manager.validate_token(token):
            return False, None

        user = auth_manager.get_username_for_token(token)
        if not user:
            return False, None

        # 5. Admin required — the terminal is admin-only (same as shell exec).
        if not auth_manager.is_admin(user):
            return False, None

        return True, user
    except Exception:
        logger.warning("PTY WebSocket auth error", exc_info=False)
        return False, None


def setup_pty_routes() -> APIRouter:
    router = APIRouter(tags=["pty"])

    @router.websocket("/ws/pty")
    async def pty_terminal(websocket: WebSocket):
        allowed, user = _resolve_ws_user(websocket)
        if not allowed:
            # Reject before accepting the socket.
            await websocket.close(code=4403)
            return

        supported, err = pty_supported()
        await websocket.accept()
        if not supported:
            await websocket.send_text(json.dumps({
                "type": "error",
                "data": f"PTY not available on this server: {err}",
            }))
            await websocket.close(code=1011)
            return

        # Optional initial sizing via query params (?cols=120&rows=30).
        try:
            qp = websocket.query_params
            cols = int(qp.get("cols", 80))
            rows = int(qp.get("rows", 24))
        except Exception:
            cols, rows = 80, 24

        session = PtySession(cwd=os.path.expanduser("~"), cols=cols, rows=rows)
        try:
            session.spawn()
        except Exception as exc:
            logger.exception("PTY spawn failed")
            await websocket.send_text(json.dumps({
                "type": "error", "data": f"Failed to start terminal: {exc}",
            }))
            await websocket.close(code=1011)
            return

        logger.info("PTY session started (backend=%s user=%s)", PTY_BACKEND, user)
        loop = asyncio.get_event_loop()
        stop = asyncio.Event()

        async def pump_output():
            """Read PTY output in a worker thread, batch ~16ms, send to client."""
            try:
                while not stop.is_set():
                    data = await loop.run_in_executor(None, session.read, 4096)
                    if data:
                        await websocket.send_text(json.dumps({
                            "type": "output", "data": data,
                        }))
                    elif not session.alive:
                        break
                    else:
                        await asyncio.sleep(_FLUSH_INTERVAL)
            except Exception:
                pass
            finally:
                code = session.exit_code if session.exit_code is not None else 0
                try:
                    await websocket.send_text(json.dumps({
                        "type": "exit", "code": code,
                    }))
                except Exception:
                    pass
                stop.set()

        out_task = asyncio.create_task(pump_output())

        try:
            while not stop.is_set():
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype == "input":
                    session.write(msg.get("data", ""))
                elif mtype == "resize":
                    session.resize(
                        int(msg.get("cols", session.cols)),
                        int(msg.get("rows", session.rows)),
                    )
                elif mtype == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("PTY WebSocket receive loop ended", exc_info=False)
        finally:
            stop.set()
            session.kill()
            out_task.cancel()
            try:
                await out_task
            except Exception:
                pass
            logger.info("PTY session ended (user=%s)", user)

    return router
