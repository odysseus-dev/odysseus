"""Regression: MCP RAG server exposes search parameters."""

import asyncio

from mcp_servers import rag_server


def test_manage_rag_schema_includes_search_query():
    tools = asyncio.run(rag_server.list_tools())

    manage_rag = next(t for t in tools if t.name == "manage_rag")
    schema = manage_rag.inputSchema

    assert "search" in schema["properties"]["action"]["enum"]
    assert "query" in schema["properties"]
    assert schema["properties"]["query"]["type"] == "string"
