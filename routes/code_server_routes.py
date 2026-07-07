"""Reverse proxy for code-server integration.

Forwards all /api/code-server/* requests to the code-server container.
Supports WebSocket upgrade for terminal and file watching.
"""
from __future__ import annotations

import asyncio
import logging
import httpx
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

    headers = dict(websocket.headers)
    headers.pop("host", None)

    async with httpx.AsyncClient(timeout=300) as client:
        try:
            async with client.stream(
                "GET",
                target_url,
                headers=headers,
            ) as resp:
                # For WebSocket upgrade, we need to handle it differently.
                # Use websockets library for proper WS proxy.
                pass
        except Exception:
            pass

    # Fallback: use a simple WebSocket bridge
    import websockets
    try:
        async with websockets.connect(target_url) as ws_server:
            async def client_to_server():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await ws_server.send(data)
                    # Also handle bytes
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def server_to_client():
                try:
                    async for msg in ws_server:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            await asyncio.gather(client_to_server(), server_to_client())
    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


def setup_code_server_routes() -> APIRouter:
    return router
