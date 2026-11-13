"""
rag_server.py

MCP server exposing RAG document management (list, add_directory, remove_directory).
"""

import asyncio
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("rag")

_rag_manager = None
_personal_docs_manager = None
_initialized = False


def _ensure_init():
    """Lazy-init RAG managers on first use."""
    global _rag_manager, _personal_docs_manager, _initialized
    if _initialized:
        return
    _initialized = True

    try:
        from src.rag_singleton import get_rag_manager
        _rag_manager = get_rag_manager()
    except Exception:
        pass

    try:
        from src.constants import PERSONAL_DIR
        from src.personal_docs import PersonalDocsManager
        _personal_docs_manager = PersonalDocsManager(PERSONAL_DIR, _rag_manager)
    except Exception:
        pass


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="manage_rag",
            description="Manage RAG indexed documents. List indexed files, add directories, or remove directories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add_directory", "remove_directory"],
                        "description": "The action to perform",
                    },
                    "directory": {"type": "string", "description": "Directory path (for add/remove)"},
                },
                "required": ["action"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "manage_rag":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    _ensure_init()
    action = arguments.get("action", "")

    if action == "list":
        return [
            TextContent(
                type="text",
                text="Error: MCP RAG listing is disabled until authenticated owner-scoped listing is available.",
            )
        ]

    elif action == "add_directory":
        _dir = arguments.get("directory")
        directory = _dir.strip() if isinstance(_dir, str) else ""
        if not directory:
            return [TextContent(type="text", text="Error: add_directory needs a directory path")]
        return [
            TextContent(
                type="text",
                text="Error: MCP directory indexing is disabled until authenticated owner-scoped indexing is available.",
            )
        ]

    elif action == "remove_directory":
        _dir = arguments.get("directory")
        directory = _dir.strip() if isinstance(_dir, str) else ""
        if not directory:
            return [TextContent(type="text", text="Error: remove_directory needs a directory path")]
        return [
            TextContent(
                type="text",
                text=(
                    "Error: MCP directory removal is disabled until "
                    "authenticated owner-scoped deletion is available. "
                    "Use the authorized application deletion workflow."
                ),
            )
        ]

    else:
        return [TextContent(type="text", text=f"Error: Unknown action '{action}'. Use: list, add_directory, remove_directory")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
