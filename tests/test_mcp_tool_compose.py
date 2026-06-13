"""MCP tool composition for OpenAI tools[] — additive merge with native Odysseus tools."""

import ast
from pathlib import Path

import pytest

from src.agent_loop import _mcp_tool_schemas_for_request

_AGENT_LOOP = Path(__file__).resolve().parent.parent / "src" / "agent_loop.py"
_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": "test"}}


MCP_ALL = [
    _schema("mcp__srv1__search_arxiv"),
    _schema("mcp__srv2__web_search"),
]
NATIVE = [_schema("ask_user"), _schema("web_search")]


class TestMcpToolSchemasForRequest:
    def test_empty_mcp_list(self):
        assert _mcp_tool_schemas_for_request([], {"mcp__srv1__search_arxiv"}) == []

    def test_legacy_none_includes_all(self):
        assert _mcp_tool_schemas_for_request(MCP_ALL, None) == MCP_ALL

    def test_explicit_empty_excludes_all(self):
        assert _mcp_tool_schemas_for_request(MCP_ALL, set()) == []

    def test_filters_to_active_only(self):
        active = {"mcp__srv1__search_arxiv"}
        out = _mcp_tool_schemas_for_request(MCP_ALL, active)
        assert len(out) == 1
        assert out[0]["function"]["name"] == "mcp__srv1__search_arxiv"

    def test_unknown_active_names_ignored(self):
        out = _mcp_tool_schemas_for_request(MCP_ALL, {"mcp__missing__tool"})
        assert out == []


class TestLowSignalMcpAdditive:
    """MCP schemas must not be filtered by _relevant_tools (native RAG lane)."""

    def test_agent_loop_mcp_not_in_relevant_tools_filter(self):
        source = _AGENT_LOOP.read_text(encoding="utf-8")
        assert "_mcp_tool_schemas_for_request(mcp_schemas, active_mcp_tools)" in source
        # MCP merge must be additive after base_schemas, not inside the native filter.
        idx_base = source.find("base_schemas = [")
        idx_mcp = source.find("mcp_for_llm = _mcp_tool_schemas_for_request")
        idx_merge = source.find("all_tool_schemas = base_schemas + mcp_for_llm")
        assert idx_base != -1 and idx_mcp != -1 and idx_merge != -1
        assert idx_base < idx_mcp < idx_merge

    def test_compose_includes_mcp_when_relevant_tools_minimal(self):
        """Simulate low_signal native set + active MCP — union, not intersection."""
        relevant = {"ask_user", "manage_memory", "ui_control"}
        base = [s for s in NATIVE if s["function"]["name"] in relevant]
        mcp = _mcp_tool_schemas_for_request(MCP_ALL, {"mcp__srv1__search_arxiv"})
        merged = base + mcp
        names = {t["function"]["name"] for t in merged}
        assert "mcp__srv1__search_arxiv" in names
        assert names <= relevant | {"mcp__srv1__search_arxiv"}


def test_chat_routes_parse_active_mcp_tools():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "def _parse_active_mcp_tools" in source
    assert "active_mcp_tools=active_mcp_tools" in source


def test_chat_routes_legacy_vs_empty():
    from routes.chat_routes import _parse_active_mcp_tools

    assert _parse_active_mcp_tools(None) is None
    assert _parse_active_mcp_tools("[]") == set()
    assert _parse_active_mcp_tools('["mcp__x__y"]') == {"mcp__x__y"}
