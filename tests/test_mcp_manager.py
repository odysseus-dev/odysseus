import asyncio
from unittest.mock import patch

from src.mcp_manager import _format_mcp_connection_error, McpManager


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Odysseus" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_http_transport_routes_to_start_http_connect():
    mgr = McpManager()

    async def fake_start(server_id, name, url):
        return "ROUTED"

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as m:
        result = asyncio.run(mgr.connect_server("id1", "n", "http", url="https://x/mcp"))
    assert result == "ROUTED"
    m.assert_called_once()
