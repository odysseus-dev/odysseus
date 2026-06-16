"""Tests for the ``manage_atlas`` agent tool.

Pins registration (handlers/tags/schema/index), the read/write/search/backlinks/
graph actions, owner-scoping via ``ctx["owner"]``, and that confinement still
holds when the agent (not a browser) drives the vault.
"""

import asyncio
import json
from pathlib import Path

import pytest

import routes.atlas_routes as ar
from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "ATLAS_ROOT", Path(tmp_path))
    ar._notes_cache.clear()
    return TOOL_HANDLERS["manage_atlas"]


def _run(coro):
    return asyncio.run(coro)


def _call(tool, owner, **args):
    return _run(tool(json.dumps(args), {"owner": owner, "session_id": None}))


def test_registered_everywhere():
    assert "manage_atlas" in TOOL_HANDLERS
    assert "manage_atlas" in TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    assert any(s["function"]["name"] == "manage_atlas" for s in FUNCTION_TOOL_SCHEMAS)
    assert "manage_atlas" in BUILTIN_TOOL_DESCRIPTIONS


def test_write_read_resolves_without_extension(tool):
    assert _call(tool, "alice", action="write", path="Roadmap", content="# Roadmap\n[[Ideas/AI]]")["exit_code"] == 0
    _call(tool, "alice", action="write", path="Ideas/AI", content="# AI\nback to [[Roadmap]]")
    # 'Roadmap' (no .md) resolves to Roadmap.md, like a wikilink would.
    r = _call(tool, "alice", action="read", path="Roadmap")
    assert r["note"]["path"] == "Roadmap.md"
    assert "Ideas/AI.md" in r["note"]["outlinks"]
    assert r["note"]["backlinks"] == ["Ideas/AI.md"]


def test_append_search_backlinks_graph(tool):
    _call(tool, "alice", action="write", path="A", content="# A\n[[B]] #tag")
    _call(tool, "alice", action="write", path="B", content="# B")

    assert _call(tool, "alice", action="append", path="B", content="more")["exit_code"] == 0
    assert _call(tool, "alice", action="search", query="#tag")["results"] == ["A.md"]
    assert _call(tool, "alice", action="backlinks", path="B")["backlinks"] == ["A.md"]
    assert len(_call(tool, "alice", action="graph")["graph"]["links"]) == 1


def test_delete(tool):
    _call(tool, "alice", action="write", path="Trash", content="bye")
    assert _call(tool, "alice", action="delete", path="Trash")["exit_code"] == 0
    assert _call(tool, "alice", action="read", path="Trash")["exit_code"] == 1


def test_confinement_and_owner_scope(tool):
    bad = _call(tool, "alice", action="write", path="../../evil", content="x")
    assert bad["exit_code"] == 1 and "path" in bad["error"].lower()

    _call(tool, "alice", action="write", path="secret", content="alice only")
    listing = _call(tool, "bob", action="list")
    assert listing["notes"] == []


def test_query_action(tool):
    _call(tool, "alice", action="write", path="daily",
          content="---\nstatus: open\n---\n# Daily\n## Todo\nx")
    _call(tool, "alice", action="write", path="done",
          content="---\nstatus: closed\n---\n# Done\n## Todo\ny")
    out = _call(tool, "alice", action="query", query={
        "from": "sections", "where": {"join": "and", "filters": [
            {"field": "section.heading", "op": "eq", "value": "todo"},
            {"field": "prop.status", "op": "eq", "value": "open"}]}})
    assert out["exit_code"] == 0
    assert [r["file.path"] for r in out["rows"]] == ["daily.md"]


def test_query_action_accepts_bare_where(tool):
    _call(tool, "alice", action="write", path="n", content="---\nstatus: open\n---\n# N")
    out = _call(tool, "alice", action="query",
                query={"filters": [{"field": "prop.status", "op": "eq", "value": "open"}]})
    assert [r["file.path"] for r in out["rows"]] == ["n.md"]


def test_query_unprefixed_field_works(tool):
    """The agent's natural 'status' (not 'prop.status') still matches."""
    _call(tool, "alice", action="write", path="n", content="---\nstatus: open\n---\n# N")
    out = _call(tool, "alice", action="query",
                query={"where": {"filters": [{"field": "status", "op": "eq", "value": "open"}]}})
    assert [r["file.path"] for r in out["rows"]] == ["n.md"]


def test_query_empty_result_hints_available_fields(tool):
    _call(tool, "alice", action="write", path="n", content="---\nstatus: open\npriority: high\n---\n# N")
    out = _call(tool, "alice", action="query",
                query={"where": {"filters": [{"field": "nope", "op": "eq", "value": "x"}]}})
    assert out["count"] == 0
    assert "prop.status" in out["available_fields"] and "prop.priority" in out["available_fields"]
    assert "prop.status" in out["response"]


def test_invalid_json(tool):
    out = _run(tool("{not json", {"owner": "alice"}))
    assert out["exit_code"] == 1
