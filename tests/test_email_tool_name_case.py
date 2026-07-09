"""Regression: local models may emit uppercase email tool names (#4769)."""

import asyncio
from types import SimpleNamespace

import src.tool_execution as te
from src.agent_tools import ToolBlock, parse_tool_blocks
from src.tool_schemas import function_call_to_tool_block
from src.tool_security import canonical_tool_type


def test_canonical_tool_type_normalizes_list_emails():
    assert canonical_tool_type("LIST_EMAILS") == "list_emails"
    assert canonical_tool_type("mcp__email__LIST_EMAILS") == "mcp__email__list_emails"


def test_function_call_uppercase_email_routes_to_mcp():
    block = function_call_to_tool_block(
        "LIST_EMAILS",
        '{"folder": "INBOX", "unread_only": true}',
    )
    assert block is not None
    assert block.tool_type == "mcp__email__list_emails"


def test_gemma_call_prefix_still_parses():
    raw = '<|tool_call|>call:LIST_EMAILS{"folder":"INBOX","unread_only":true}<|tool_call|>'
    blocks = parse_tool_blocks(raw)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__list_emails"


def test_execute_uppercase_list_emails_dispatches_mcp(monkeypatch):
    class FakeMcp:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            return {"output": "ok", "exit_code": 0}

    fake = FakeMcp()
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    monkeypatch.setattr(te, "get_mcp_manager", lambda: fake)

    block = ToolBlock("LIST_EMAILS", '{"folder": "INBOX", "unread_only": true}')
    desc, result = asyncio.run(
        te.execute_tool_block(block, owner="admin", session_id="s1")
    )
    assert result.get("exit_code") == 0
    assert fake.calls
    assert fake.calls[0][0] == "mcp__email__list_emails"
    assert "list_emails" in desc