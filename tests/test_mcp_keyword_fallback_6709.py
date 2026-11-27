"""Regression tests for user-added MCP tool selection on tool-RAG timeout (#6709)."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from src import agent_loop
from src.tool_index import ToolIndex, matching_user_mcp_tools


class _FakeMcpManager:
    def __init__(self) -> None:
        self._generation = 1
        self._connections = {
            "e6d3b43e": {"name": "Obsidian", "status": "connected", "identity": ""},
            "email": {"name": "Built-in: Email", "status": "connected", "identity": ""},
            "offline01": {"name": "OfflineVault", "status": "disconnected", "identity": ""},
        }
        self._tools = {
            "e6d3b43e": [
                {
                    "name": "read_text_file",
                    "description": "Read the complete contents of a text file from the vault",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
                {
                    "name": "list_directory",
                    "description": "Get a detailed listing of all files and directories",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
                {
                    "name": "search_files",
                    "description": "Recursively search for files and directories matching a pattern",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
                {
                    "name": "write_file",
                    "description": "Create a new file or completely overwrite an existing file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            ],
            "email": [
                {
                    "name": "list_emails",
                    "description": "List inbox messages",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "offline01": [
                {
                    "name": "read_offline",
                    "description": "Read offline Obsidian vault file",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        }

    @staticmethod
    def is_builtin(server_id: str) -> bool:
        return server_id in {"email", "memory", "rag", "image_gen"} or server_id.startswith("builtin_")

    def get_all_statuses(self) -> dict:
        return dict(self._connections)

    def get_all_tools(self, disabled_map=None) -> list[dict]:
        result = []
        for server_id, tools in self._tools.items():
            conn = self._connections.get(server_id, {})
            disabled = (disabled_map or {}).get(server_id, set())
            for tool in tools:
                result.append(
                    {
                        "server_id": server_id,
                        "server_name": conn.get("name", server_id),
                        "name": tool["name"],
                        "qualified_name": f"mcp__{server_id}__{tool['name']}",
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema") or {},
                        "is_disabled": tool["name"] in disabled,
                    }
                )
        return result

    def get_all_openai_schemas(self, disabled_map=None) -> list[dict]:
        schemas = []
        for item in self.get_all_tools(disabled_map):
            if item["is_disabled"] or item["server_id"] == "offline01":
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": item["qualified_name"],
                        "description": f"[MCP:{item['server_name']}] {item['description']}",
                        "parameters": item["input_schema"],
                    },
                }
            )
        return schemas

    def get_tool_descriptions_for_prompt(self, disabled_map=None, allowed_names=None) -> str:
        lines = []
        for item in self.get_all_tools(disabled_map):
            if item["is_disabled"] or item["server_id"] == "offline01":
                continue
            q = item["qualified_name"]
            if allowed_names is not None and q not in allowed_names and item["name"] not in allowed_names:
                continue
            lines.append(f"- {q}: {item['description']}")
        return "\n".join(lines)


def test_matching_user_mcp_tools_selects_named_server_and_respects_disabled_and_builtins() -> None:
    mgr = _FakeMcpManager()
    matched = matching_user_mcp_tools(
        "What does my About Me note in the Obsidian vault say about me? Use the Obsidian tools.",
        mgr,
        disabled_map={"e6d3b43e": {"write_file"}},
    )
    assert "mcp__e6d3b43e__read_text_file" in matched
    assert "mcp__e6d3b43e__list_directory" in matched
    assert "mcp__e6d3b43e__search_files" in matched
    assert "mcp__e6d3b43e__write_file" not in matched
    assert "mcp__email__list_emails" not in matched
    assert "mcp__offline01__read_offline" not in matched


def test_matching_user_mcp_tools_matches_tool_keywords_without_server_name() -> None:
    mgr = _FakeMcpManager()
    matched = matching_user_mcp_tools(
        "Recursively search for files in my vault directory",
        mgr,
    )
    assert "mcp__e6d3b43e__search_files" in matched
    assert "mcp__e6d3b43e__list_directory" in matched


def test_tool_index_get_tools_for_query_includes_user_mcp_even_when_reindex_skipped() -> None:
    mgr = _FakeMcpManager()
    idx = ToolIndex.__new__(ToolIndex)
    idx._lanes = []
    idx._mcp_generation = -1
    idx.retrieve = lambda query, k=8: []
    idx._last_mcp_mgr = mgr
    idx._last_mcp_disabled_map = {}

    tools = idx.get_tools_for_query("Use the Obsidian tools to read my About Me note")
    assert "mcp__e6d3b43e__read_text_file" in tools
    assert "mcp__e6d3b43e__list_directory" in tools


async def test_stream_agent_loop_keyword_fallback_includes_user_mcp_on_rag_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _FakeMcpManager()
    monkeypatch.setattr(agent_loop, "_TOOL_SELECTION_TIMEOUT_SECONDS", 0.05)

    class _SlowToolIndex:
        def index_mcp_tools(self, *args, **kwargs):
            time.sleep(0.2)

        def get_tools_for_query(self, *args, **kwargs):
            time.sleep(0.2)
            return set()

    import src.tool_index as ti
    import src.tool_utils as tu

    monkeypatch.setattr(ti, "get_tool_index", lambda: _SlowToolIndex())
    monkeypatch.setattr(tu, "get_mcp_manager", lambda: mgr)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: mgr)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    captured_audits: list[dict] = []

    async def _fake_stream_llm_with_fallback(*args, **kwargs):
        yield "data: " + json.dumps(
            {"choices": [{"delta": {"content": "Checked."}, "finish_reason": "stop"}]}
        ) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", _fake_stream_llm_with_fallback)

    events = []
    async for chunk in agent_loop.stream_agent_loop(
        endpoint_url="http://127.0.0.1:11434/v1",
        model="qwen3:14b",
        messages=[
            {
                "role": "user",
                "content": "What does my About Me note in the Obsidian vault say about me? Use the Obsidian tools.",
            }
        ],
        max_rounds=1,
        owner="admin",
    ):
        events.append(chunk)
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            payload = json.loads(chunk[6:])
            if payload.get("type") == "tool_routing_audit":
                captured_audits.append(payload)

    assert captured_audits, "Expected tool_routing_audit SSE event"
    audit = captured_audits[0]
    assert "mcp__e6d3b43e__read_text_file" in audit["retrieved_tools"]
    assert "mcp__e6d3b43e__list_directory" in audit["retrieved_tools"]
    assert "mcp__e6d3b43e__read_text_file" not in audit["intentionally_excluded_tools"]
