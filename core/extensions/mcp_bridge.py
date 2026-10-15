"""mcp_bridge.py — register OXS extension tools with McpManager.

OXS extensions can register MCP tools via ``host.register_mcp_tool``.
PR 3 wires those registrations into the existing McpManager so the
agent sees them as ``mcp__<server>__<tool>`` like any other MCP tool.

Each extension gets its own ``server_id`` (``oxs_<extension_id>``). The
host records registrations on enable, and the registry calls
``install_tools`` to push them into McpManager. On disable the registry
calls ``uninstall_tools`` to remove them.

We use the existing McpManager.register_in_process_tool API (added in
this PR) — extensions do not spawn subprocesses. Handlers can be sync or
async.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .host import MCPToolRegistration

logger = logging.getLogger(__name__)


def _server_id(extension_id: str) -> str:
    """Build the namespaced MCP server id for an extension."""
    # Note: McpManager qualified tool names use ``__`` as separator, so
    # the server id itself cannot contain ``__``. We sanitize by
    # replacing it. Extension ids already follow [a-z0-9_-] so this is
    # defensive.
    return f"oxs_{extension_id}".replace("__", "_")


def install_tools(
    extension_id: str,
    registrations: list[MCPToolRegistration],
) -> int:
    """Push every registration onto the shared McpManager singleton.

    ``registrations`` is a list of
    :class:`core.extensions.host.MCPToolRegistration` objects.

    Returns the number of tools installed.
    """
    from src.mcp_manager import get_mcp_manager

    manager = get_mcp_manager()
    server_id = _server_id(extension_id)
    count = 0
    for reg in registrations:
        qualified_name = f"oxs.{extension_id}.{reg.tool_name}"
        # Prefer the semantic description when the extension supplied one —
        # ``tool_index`` RAG ranks MCP tools by these strings, so a useful
        # description matters for retrieval. Fall back to the legacy
        # ``"[<ext_id>] <name>"`` placeholder for backward compatibility.
        description = reg.description_rich or f"[{extension_id}] {reg.tool_name}"
        try:
            manager.register_in_process_tool(
                server_id=server_id,
                tool_name=reg.tool_name,
                input_schema=reg.schema or {"type": "object", "properties": {}},
                description=description,
                handler=reg.handler,
                read_only=bool(getattr(reg, "read_only", False)),
            )
            count += 1
        except Exception:
            logger.exception(
                "OXS install mcp tool %s failed", qualified_name,
            )
    return count


def uninstall_tools(extension_id: str) -> int:
    """Remove every tool belonging to ``extension_id``."""
    from src.mcp_manager import get_mcp_manager

    manager = get_mcp_manager()
    server_id = _server_id(extension_id)
    return manager.unregister_in_process_tools(server_id)


__all__ = ["install_tools", "uninstall_tools"]