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
        "query": {"from": "sections", "where": {"join": "and", "filters": [
            {"field": "section.heading", "op": "eq", "value": "todo"},
            {"field": "prop.status", "op": "eq", "value": "open"}]}},
    }))
    payload = json.loads(out[0].text)
    assert [r["file.path"] for r in payload["rows"]] == ["daily.md"]


def test_call_tool_accepts_json_string_query(vault):
    import mcp_servers.atlas_server as srv
    out = asyncio.run(srv.call_tool("query_atlas", {
        "query": json.dumps({"where": {"filters": [{"field": "prop.status", "op": "eq", "value": "closed"}]}}),
    }))
    payload = json.loads(out[0].text)
    assert [r["file.path"] for r in payload["rows"]] == ["done.md"]


def test_caller_supplied_owner_is_ignored(vault, monkeypatch):
    """Security: a client cannot read another user's vault by passing `owner`.

    Notes are seeded only under 'default'; 'alice' has her own (separate) note.
    A query naming owner='alice' must NOT return alice's vault — owner is bound
    from the environment, not the tool arguments.
    """
    import mcp_servers.atlas_server as srv
    ar.write_note("alice", "alice-secret", "---\nstatus: open\n---\n# Secret")
    monkeypatch.delenv("ODYSSEUS_MCP_ATLAS_OWNER", raising=False)
    monkeypatch.delenv("ODYSSEUS_ATLAS_OWNER", raising=False)
    out = asyncio.run(srv.call_tool("query_atlas", {
        "owner": "alice",  # attacker-supplied — must be ignored
        "query": {"where": {"filters": [{"field": "status", "op": "eq", "value": "open"}]}},
    }))
    paths = [r["file.path"] for r in json.loads(out[0].text)["rows"]]
    assert "alice-secret.md" not in paths      # alice's vault not reachable
    assert paths == ["daily.md"]               # only the 'default' vault is served


def test_env_scopes_owner(vault, monkeypatch):
    import mcp_servers.atlas_server as srv
    ar.write_note("bob", "bob-note", "---\nstatus: open\n---\n# Bob")
    monkeypatch.setenv("ODYSSEUS_MCP_ATLAS_OWNER", "bob")
    out = asyncio.run(srv.call_tool("query_atlas", {
        "query": {"where": {"filters": [{"field": "status", "op": "eq", "value": "open"}]}},
    }))
    assert [r["file.path"] for r in json.loads(out[0].text)["rows"]] == ["bob-note.md"]


def test_unknown_tool():
    import mcp_servers.atlas_server as srv
    out = asyncio.run(srv.call_tool("nope", {}))
    assert "Unknown tool" in out[0].text
