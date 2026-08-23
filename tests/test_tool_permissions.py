import asyncio

import pytest

from src.tool_approval import (
    create_approval,
    normalize_permission_mode,
    resolve_approval,
    tool_is_read_only,
    tool_requires_approval,
)


def test_permission_modes_fail_to_auto_for_unknown_values():
    assert normalize_permission_mode("auto") == "auto"
    assert normalize_permission_mode("ask_all") == "ask_all"
    assert normalize_permission_mode("something-new") == "auto"


def test_ask_actions_allows_reads_but_pauses_changes():
    assert not tool_requires_approval("read_file", "ask_actions")
    assert tool_requires_approval("write_file", "ask_actions")
    assert tool_requires_approval("bash", "ask_actions")


def test_ask_all_pauses_reads_and_read_only_classifies_aliases():
    assert tool_requires_approval("read_file", "ask_all")
    assert tool_is_read_only("mcp__email__read_email")
    assert not tool_is_read_only("edit_file")


@pytest.mark.asyncio
async def test_approval_can_only_be_resolved_by_its_owner():
    approval_id, future = create_approval("alice", "session-1", "write_file")
    assert not resolve_approval(approval_id, "bob", True)
    assert not future.done()
    assert resolve_approval(approval_id, "alice", True)
    assert await asyncio.wait_for(future, timeout=0.1) is True


@pytest.mark.asyncio
async def test_read_only_is_enforced_at_dispatcher(tmp_path):
    from src.agent_tools import ToolBlock
    from src.tool_execution import execute_tool_block

    target = tmp_path / "blocked.txt"
    _, result = await execute_tool_block(
        ToolBlock("write_file", '{"path":"blocked.txt","content":"nope"}'),
        workspace=str(tmp_path),
        permission_mode="read_only",
    )
    assert result["blocked"] is True
    assert not target.exists()
