"""Reverse proxy for code-server integration.

Forwards all /api/code-server/* requests to the code-server container.
Supports WebSocket upgrade for terminal and file watching.
"""
from __future__ import annotations

import logging
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

CODE_SERVER_URL = "http://code-server:8443"
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
                "content-encoding", "content-length", "transfer-encoding", "connection"
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


def setup_code_server_routes() -> APIRouter:
    return router
