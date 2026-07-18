"""Regression guard for #5557 — manage_rag must be dispatchable, not just advertised.

The tool was listed in the UI's TOOL_GROUPS and had a live handler
(ai_interaction.do_manage_rag, still receiving fixes as late as #1660), but was
absent from TOOL_TAGS and from every dispatch site, so agent tool blocks naming
it dead-ended as unknown before reaching any handler.

These tests pin the full path: fence tag recognized -> execute_tool_block routes
to do_manage_rag -> non-admin gating matches its manage_memory-class sensitivity
(add_directory indexes arbitrary server paths into the shared RAG index).
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from src.agent_tools import TOOL_TAGS, ToolBlock
from src.tool_execution import execute_tool_block
from src.tool_security import is_public_blocked_tool
import src.ai_interaction as ai
import src.tool_execution as tool_execution


def _single_user_mode(monkeypatch):
    """Simulate the self-host default (auth disabled) so gating passes.

    tool_execution imports the auth helpers by name, so patch its bindings.
    """
    monkeypatch.setattr(
        tool_execution, "owner_is_admin_or_single_user", lambda owner: True, raising=False
    )
    monkeypatch.setattr(
        tool_execution, "is_public_blocked_tool", lambda tool: False
    )


def test_manage_rag_is_a_recognized_tool_tag():
    assert "manage_rag" in TOOL_TAGS


def test_manage_rag_blocked_for_non_admin_users():
    # Same class as manage_memory/read_file: touches the shared index and
    # indexes server filesystem paths, so public users must not reach it.
    assert is_public_blocked_tool("manage_rag")


async def test_execute_tool_block_dispatches_manage_rag(monkeypatch):
    _single_user_mode(monkeypatch)
    seen = {}

    async def fake_do_manage_rag(content, session_id=None):
        seen["content"] = content
        seen["session_id"] = session_id
        return {"results": "stub"}

    monkeypatch.setattr(ai, "do_manage_rag", fake_do_manage_rag)

    desc, result = await execute_tool_block(
        ToolBlock(tool_type="manage_rag", content="list"),
        session_id="sess-1",
        owner=None,
    )

    assert seen == {"content": "list", "session_id": "sess-1"}
    assert result == {"results": "stub"}
    assert desc.startswith("manage_rag: list")


async def test_manage_rag_respects_user_disable(monkeypatch):
    _single_user_mode(monkeypatch)

    async def fake_do_manage_rag(content, session_id=None):  # pragma: no cover
        raise AssertionError("handler must not run when tool is disabled")

    monkeypatch.setattr(ai, "do_manage_rag", fake_do_manage_rag)

    desc, result = await execute_tool_block(
        ToolBlock(tool_type="manage_rag", content="list"),
        session_id="sess-1",
        disabled_tools={"manage_rag"},
        owner=None,
    )

    assert "BLOCKED" in desc
    assert "disabled" in result["error"]
