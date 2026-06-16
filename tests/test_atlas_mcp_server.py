"""The Atlas MCP server (query_atlas) + builtin registration."""

import asyncio
import json
from pathlib import Path

import pytest

import routes.atlas_routes as ar


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "ATLAS_ROOT", Path(tmp_path))
    ar._notes_cache.clear()
    ar.write_note("default", "daily", "---\nstatus: open\n---\n# Daily\n## Todo\nx")
    ar.write_note("default", "done", "---\nstatus: closed\n---\n# Done\n## Todo\ny")
    return tmp_path


def test_registered_in_builtin_servers():
    from src.builtin_mcp import _BUILTIN_SERVERS
    assert _BUILTIN_SERVERS.get("atlas") == ("mcp_servers/atlas_server.py", "Built-in: Atlas")


def test_list_tools_exposes_query_atlas():
    import mcp_servers.atlas_server as srv
    tools = asyncio.run(srv.list_tools())
    assert [t.name for t in tools] == ["query_atlas"]
    assert "query" in tools[0].inputSchema["properties"]


def test_call_tool_runs_query(vault):
    import mcp_servers.atlas_server as srv
    out = asyncio.run(srv.call_tool("query_atlas", {
        "owner": "default",
        "query": {"from": "sections", "where": {"join": "and", "filters": [
            {"field": "section.heading", "op": "eq", "value": "todo"},
            {"field": "prop.status", "op": "eq", "value": "open"}]}},
    }))
    payload = json.loads(out[0].text)
    assert [r["file.path"] for r in payload["rows"]] == ["daily.md"]


def test_call_tool_accepts_json_string_query(vault):
    import mcp_servers.atlas_server as srv
    out = asyncio.run(srv.call_tool("query_atlas", {
        "owner": "default",
        "query": json.dumps({"where": {"filters": [{"field": "prop.status", "op": "eq", "value": "closed"}]}}),
    }))
    payload = json.loads(out[0].text)
    assert [r["file.path"] for r in payload["rows"]] == ["done.md"]


def test_unknown_tool():
    import mcp_servers.atlas_server as srv
    out = asyncio.run(srv.call_tool("nope", {}))
    assert "Unknown tool" in out[0].text
