"""Regression for MCP server inventory routing on local text-only models.

A request to list installed MCP servers must use the native manage_mcp inventory
path. It must not expose unrelated browser MCP schemas that invite fabricated
browser navigation.
"""

from src.agent_loop import _is_mcp_server_inventory_request


def test_installed_mcps_servers_is_an_inventory_request():
    assert _is_mcp_server_inventory_request("what mcps servers are installed in odysseus")


def test_list_connected_mcp_servers_is_an_inventory_request():
    assert _is_mcp_server_inventory_request("list the connected MCP servers")


def test_browser_request_is_not_an_mcp_inventory_request():
    assert not _is_mcp_server_inventory_request("open this website in the browser")


def test_mcp_tool_request_is_not_server_inventory_request():
    assert not _is_mcp_server_inventory_request("what MCP tools can the browser server use")
