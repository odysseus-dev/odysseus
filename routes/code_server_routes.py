"""Reverse proxy for code-server integration.

Forwards all /api/code-server/* requests to the code-server container.
Supports WebSocket upgrade for terminal and file watching.
"""
from __future__ import annotations

import asyncio
import logging
import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

CODE_SERVER_URL = "http://code-server:8443"
CODE_SERVER_WS_URL = "ws://code-server:8443"
router = APIRouter(tags=["code-server"])


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_code_server(path: str, request: Request):
    """Proxy HTTP requests to code-server."""
    target_url = f"{CODE_SERVER_URL}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)

    body = await request.body()

    async with httpx.AsyncClient(timeout=300) as client:
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
                "x-frame-options", "content-security-policy", "content-security-policy-report-only",
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
                content=b'{"error": "code-server is not running. Start it with: docker compose up -d code-server"}',
                status_code=502,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"code-server proxy error: {e}")
            return Response(
                content=f'{{"error": "{str(e)}"}}'.encode(),
                status_code=502,
                media_type="application/json",
            )


@router.websocket("/{path:path}")
async def proxy_code_server_ws(websocket: WebSocket, path: str):
    """Proxy WebSocket connections to code-server."""
    target_url = f"{CODE_SERVER_WS_URL}/{path}"
    if websocket.query_params:
        qs = "&".join(f"{k}={v}" for k, v in websocket.query_params.items())
        target_url += f"?{qs}"

    await websocket.accept()

    # Build headers from the original WebSocket request — skip Origin
    # (code-server rejects mismatched origins; trusted-origins config
    # only works for some code-server versions)
    extra_headers = {}
    for key in ("cookie", "authorization"):
        val = websocket.headers.get(key)
        if val:
            extra_headers[key] = val

    try:
        async with websockets.connect(
            target_url,
            additional_headers=extra_headers,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as ws_server:
            async def client_to_server():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.receive":
                            if "text" in msg:
                                await ws_server.send(msg["text"])
                            elif "bytes" in msg:
                                await ws_server.send(msg["bytes"])
                        elif msg["type"] == "websocket.disconnect":
                            break
                except (WebSocketDisconnect, Exception):
                    pass

            async def server_to_client():
                try:
                    async for msg in ws_server:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        elif isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_server()),
                    asyncio.create_task(server_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


def setup_code_server_routes() -> APIRouter:
    return router
