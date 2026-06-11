"""Verify that SSE MCP connections forward the env dict as HTTP headers.

For SSE transport there is no subprocess to receive environment variables, so
McpManager.connect_server repurposes the stored env JSON as request headers
(e.g. an Authorization bearer token for the Docker MCP gateway).
"""

import asyncio
from unittest.mock import AsyncMock, patch

from src.mcp_manager import McpManager


def test_connect_server_sse_passes_env_as_headers():
    mgr = McpManager()
    mgr._connect_sse = AsyncMock(return_value=True)

    ok = asyncio.run(mgr.connect_server(
        server_id="srv-sse",
        name="gateway",
        transport="sse",
        env={"Authorization": "Bearer sekret"},
        url="http://host.docker.internal:8811/sse",
    ))

    assert ok is True
    mgr._connect_sse.assert_called_once_with(
        "srv-sse", "gateway", "http://host.docker.internal:8811/sse",
        headers={"Authorization": "Bearer sekret"},
    )


def test_connect_server_sse_empty_env_passes_none():
    mgr = McpManager()
    mgr._connect_sse = AsyncMock(return_value=True)

    asyncio.run(mgr.connect_server(
        server_id="srv-sse",
        name="gateway",
        transport="sse",
        env={},
        url="http://localhost:8811/sse",
    ))

    mgr._connect_sse.assert_called_once_with(
        "srv-sse", "gateway", "http://localhost:8811/sse", headers=None,
    )


def test_connect_sse_forwards_headers_to_sse_client():
    """_connect_sse must hand the headers dict to mcp.client.sse.sse_client."""
    mgr = McpManager()

    captured = {}

    class _FakeCtx:
        def __init__(self, result):
            self._result = result

        async def __aenter__(self):
            return self._result

        async def __aexit__(self, *exc):
            return False

    def fake_sse_client(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeCtx((AsyncMock(), AsyncMock()))

    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()
    fake_session.list_tools = AsyncMock(return_value=type("R", (), {"tools": []})())

    with patch("mcp.client.sse.sse_client", fake_sse_client), \
         patch("mcp.ClientSession", lambda r, w: _FakeCtx(fake_session)):
        ok = asyncio.run(mgr._connect_sse(
            "srv-sse", "gateway", "http://localhost:8811/sse",
            headers={"Authorization": "Bearer sekret"},
        ))

    assert ok is True
    assert captured["url"] == "http://localhost:8811/sse"
    assert captured["headers"] == {"Authorization": "Bearer sekret"}
