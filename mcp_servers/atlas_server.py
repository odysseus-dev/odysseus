"""
atlas_server.py

MCP server exposing the Atlas vault's Bases query engine, so external MCP
clients (and agentic flows) can run structured queries over the markdown vault —
e.g. "select the sections titled 'todo' where status is still open".

The query shape is identical to the in-app `manage_atlas` tool's `query` action
and the HTTP `POST /api/atlas/query` route (all three call
``src.atlas_query.run_query``), so a query the user designs in the Bases UI can
be handed to an agent verbatim.

Queries are confined to a single owner's vault (``owner`` argument, defaulting
to the single-user vault) via the same ``safe_atlas_path`` machinery the routes
use — a query can never read outside the vault directory.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("atlas")


def _query_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "object",
                "description": (
                    "Structured Bases query. Keys: from ('notes'|'sections'), "
                    "where {join:'and'|'or', filters:[{field, op, value}]}, "
                    "select [cols], computed {name: expr}, sort [{field,dir}], "
                    "group_by, view, limit. Fields: file.path/title/tags/mtime/"
                    "folder, prop.<name>, section.heading/body/level. Ops: eq ne "
                    "contains startswith endswith regex gt lt gte lte exists empty in."
                ),
            },
            "owner": {
                "type": "string",
                "description": "Vault owner (optional; defaults to the single-user vault).",
            },
        },
        "required": ["query"],
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_atlas",
            description=(
                "Query the Atlas markdown vault (Obsidian-style notes) by frontmatter "
                "properties, tags, title/path, and heading sections. Returns matching "
                "rows. Use from='sections' to target heading sections (e.g. sections "
                "titled 'todo'); from='notes' for whole-note queries."
            ),
            inputSchema=_query_schema(),
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "query_atlas":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        # Imported lazily so a failed import surfaces as a tool error, not a
        # server-start crash. notes_for_query confines to the owner's vault.
        from routes.atlas_routes import notes_for_query
        from src.atlas_query import run_query

        owner = arguments.get("owner") or None
        query = arguments.get("query") or {}
        if isinstance(query, str):
            query = json.loads(query)
        result = run_query(query, notes_for_query(owner))
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
