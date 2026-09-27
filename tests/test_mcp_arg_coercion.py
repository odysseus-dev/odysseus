"""MCP tool arguments are coerced to the types the tool schema declares.

Models emit ``"limit": "5"``; Firecrawl's strict validation rejects that with
MCP error -32602 ("expected number, received string").
"""
import asyncio

import pytest

mcp_manager = pytest.importorskip("src.mcp_manager")
_coerce = mcp_manager._coerce_mcp_args

SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "number"},
        "k": {"type": "integer"},
        "ratio": {"type": "number"},
        "onlyMain": {"type": "boolean"},
        "maybe": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "sources": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}}}},
        "opts": {"type": "object", "properties": {"timeout": {"type": "integer"}}},
    },
}


def test_scalars_coerced():
    out = _coerce(
        {"query": "5", "limit": "5", "k": " 3 ", "ratio": "0.5", "onlyMain": "true", "maybe": "7"},
        SCHEMA,
    )
    assert out == {"query": "5", "limit": 5, "k": 3, "ratio": 0.5, "onlyMain": True, "maybe": 7}
    assert isinstance(out["limit"], int)


def test_nested_and_json_encoded():
    out = _coerce({"sources": '[{"n": "2"}]', "opts": {"timeout": "30"}}, SCHEMA)
    assert out == {"sources": [{"n": 2}], "opts": {"timeout": 30}}


def test_unparseable_and_unknown_pass_through():
    args = {"limit": "many", "onlyMain": "perhaps", "extra": "1", "k": 4}
    assert _coerce(args, SCHEMA) == args
    assert _coerce(args, {}) == args
    assert _coerce("raw", SCHEMA) == "raw"


def test_call_tool_sends_coerced_args():
    mgr = mcp_manager.McpManager()
    seen = {}

    class _Session:
        async def call_tool(self, name, arguments, read_timeout_seconds=None):
            seen["args"] = arguments

            class _R:
                content = []
                isError = False
            return _R()

    mgr._sessions["srv"] = _Session()
    mgr._tools["srv"] = [{"name": "firecrawl_search", "input_schema": SCHEMA}]
    asyncio.run(mgr.call_tool("mcp__srv__firecrawl_search", {"query": "x", "limit": "2"}))
    assert seen["args"] == {"query": "x", "limit": 2}
