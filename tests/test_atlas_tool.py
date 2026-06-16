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


def test_invalid_json(tool):
    out = _run(tool("{not json", {"owner": "alice"}))
    assert out["exit_code"] == 1
