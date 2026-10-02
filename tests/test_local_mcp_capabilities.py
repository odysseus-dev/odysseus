"""Declared local MCP tools: ODYSSEUS_LOCAL_MCP_READ_TOOLS / _WRITE_TOOLS.

A user-configured MCP server's tools are absent from TOOL_CAPABILITIES, so
they are unknown and fail high. For a server with a read and a write tool that
combination is unusable: the read result arms the post-external gate, because
an unknown result is not SYSTEM integrity, and the write that follows is
refused until the user authorizes it separately — every second call.

These tests pin both halves: a declared read no longer arms the gate, and a
declared write is still gated once genuinely external content has entered the
run. With the variables unset, nothing changes.
"""

import pytest

from src.tool_capabilities import (
    LOCAL_MCP_READ_TOOLS_ENV,
    LOCAL_MCP_WRITE_TOOLS_ENV,
    ResultIntegrity,
    ToolEffect,
    ToolRunSecurityContext,
    _local_mcp_tools,
    capabilities_for_tool,
    tool_result_should_arm_gate,
)

READ_TOOL = "mcp__a1b2c3d4__search_notes"
WRITE_TOOL = "mcp__a1b2c3d4__create_note"
FOREIGN_TOOL = "mcp__e5f6a7b8__fetch_page"

# A plain successful MCP result: content that will be folded into the model's
# context, which is what decides whether the gate arms.
RESULT = {"success": True, "text": "1 match: Notes/Example.md"}


@pytest.fixture(autouse=True)
def _clear_declaration_cache():
    _local_mcp_tools.cache_clear()
    yield
    _local_mcp_tools.cache_clear()


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setenv(LOCAL_MCP_READ_TOOLS_ENV, f" {READ_TOOL} , ")
    monkeypatch.setenv(LOCAL_MCP_WRITE_TOOLS_ENV, WRITE_TOOL)
    _local_mcp_tools.cache_clear()
    yield


def test_declared_read_tool_is_known_and_system_integrity(declared):
    capabilities = capabilities_for_tool(READ_TOOL)
    assert capabilities.known is True
    assert capabilities.effects == frozenset({ToolEffect.READ_PRIVATE})
    assert capabilities.result_integrity is ResultIntegrity.SYSTEM


def test_declared_write_tool_is_known_and_write_private(declared):
    capabilities = capabilities_for_tool(WRITE_TOOL)
    assert capabilities.known is True
    assert capabilities.effects == frozenset({ToolEffect.WRITE_PRIVATE})


def test_declared_read_result_does_not_arm_gate(declared):
    assert tool_result_should_arm_gate(READ_TOOL, RESULT) is False


def test_declared_write_allowed_after_declared_read(declared):
    context = ToolRunSecurityContext()
    context.observe_tool_result(READ_TOOL, RESULT)
    assert context.external_untrusted_context_seen is False
    assert context.decision_for(WRITE_TOOL).allowed is True


def test_declared_write_still_gated_after_external_content(declared):
    """The declaration must not widen the gate for a genuinely tainted run."""
    context = ToolRunSecurityContext()
    context.observe_tool_result(FOREIGN_TOOL, RESULT)
    assert context.external_untrusted_context_seen is True
    decision = context.decision_for(WRITE_TOOL)
    assert decision.allowed is False
    assert "write_private" in (decision.reason or "")


def test_undeclared_server_keeps_failing_high(monkeypatch):
    """Unset variables mean unchanged behaviour: this is the pre-patch path."""
    monkeypatch.delenv(LOCAL_MCP_READ_TOOLS_ENV, raising=False)
    monkeypatch.delenv(LOCAL_MCP_WRITE_TOOLS_ENV, raising=False)
    _local_mcp_tools.cache_clear()

    assert capabilities_for_tool(READ_TOOL).known is False
    assert tool_result_should_arm_gate(READ_TOOL, RESULT) is True

    context = ToolRunSecurityContext()
    context.observe_tool_result(READ_TOOL, RESULT)
    assert context.decision_for(WRITE_TOOL).allowed is False


def test_declaration_does_not_leak_to_other_servers(declared):
    assert capabilities_for_tool(FOREIGN_TOOL).known is False
    assert tool_result_should_arm_gate(FOREIGN_TOOL, RESULT) is True
