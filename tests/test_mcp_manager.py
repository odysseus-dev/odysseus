import asyncio
from unittest.mock import patch

from src.mcp_manager import _format_mcp_connection_error, _http_auth_headers_from_env, McpManager


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


def test_http_transport_with_static_auth_awaits_direct_connect():
    mgr = McpManager()

    with patch.object(McpManager, "_connect_http", return_value=True) as m:
        result = asyncio.run(mgr.connect_server(
            "id1", "n", "http", url="https://x/mcp", env={"GITHUB_TOKEN": "t"},
        ))
    assert result is True
    m.assert_called_once_with("id1", "n", "https://x/mcp", {"GITHUB_TOKEN": "t"})


def test_http_transport_without_auth_schedules_background_connect():
    mgr = McpManager()

    with patch.object(McpManager, "schedule_http_connect") as m:
        result = asyncio.run(mgr.connect_server(
            "id1", "n", "http", url="https://x/mcp", env={},
        ))
    assert result is False
    m.assert_called_once_with("id1", "n", "https://x/mcp", {})


def test_http_auth_headers_from_github_pat():
    headers = _http_auth_headers_from_env({"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_test"})
    assert headers == {"Authorization": "Bearer ghp_test"}
