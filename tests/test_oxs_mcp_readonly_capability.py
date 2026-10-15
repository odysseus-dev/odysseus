"""OXS read-only MCP tools must be admissible for unattended background tasks.

Three things have to line up for a scheduled OXS task (the whats2api bridge
analyzer) to actually read a message:

1. The tool's catalog is injected into the prompt as untrusted control-plane
   context, which arms ``ToolRunSecurityContext.external_untrusted_context_seen``.
2. Once armed, ``decision_for`` refuses any tool whose capabilities are
   *unknown* — and every OXS in-process tool is unknown unless the extension
   declares it. ``read_only=True`` on ``register_mcp_tool`` publishes a
   brokered-private-read classification.
3. Even a declared read is refused after the *first* bridge result lands in
   context, because ``READ_PRIVATE`` is in ``POST_EXTERNAL_BLOCKED_EFFECTS``.
   A scheduled task has no user to answer the approval card, so the gate can
   only ever fail it permanently. ``declared_task_tools`` (server-owned,
   background workloads only) is the narrow escape hatch for that case.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.tool_capabilities import (  # noqa: E402
    POST_EXTERNAL_BLOCKED_EFFECTS,
    ToolEffect,
    ToolRunSecurityContext,
    capabilities_for_tool,
    declare_mcp_tool_capabilities,
    undeclare_mcp_server_capabilities,
)

SERVER_ID = "oxs_whats2api_bridge"
QUALIFIED = f"mcp__{SERVER_ID}__list_messages"


@pytest.fixture(autouse=True)
def _clean_declaration():
    undeclare_mcp_server_capabilities(SERVER_ID)
    yield
    undeclare_mcp_server_capabilities(SERVER_ID)


def _armed_context(*extra_sources: str) -> ToolRunSecurityContext:
    """A context whose gate has already fired, as it is mid-run."""
    ctx = ToolRunSecurityContext(
        external_untrusted_context_seen=True,
        external_sources=["mcp tools", *extra_sources],
    )
    return ctx


def test_undeclared_oxs_tool_fails_high():
    assert capabilities_for_tool(QUALIFIED).known is False
    decision = _armed_context().decision_for(QUALIFIED, "{}")
    assert decision.allowed is False
    assert "unknown" in (decision.reason or "")


def test_read_only_declaration_admits_the_tool():
    declare_mcp_tool_capabilities(QUALIFIED, read_only=True)
    caps = capabilities_for_tool(QUALIFIED)
    assert caps.known is True
    assert caps.effects == frozenset({ToolEffect.READ_PRIVATE})
    # The result must stay untrusted — declaring read-only is about the
    # *call*, never about treating remote payload as system-trusted.
    from src.tool_capabilities import ResultIntegrity
    assert caps.result_integrity is ResultIntegrity.EXTERNAL_UNTRUSTED

    decision = _armed_context().decision_for(QUALIFIED, "{}")
    assert decision.allowed is True, decision.reason


def test_read_only_declaration_does_not_leak_to_writes():
    declare_mcp_tool_capabilities(QUALIFIED, read_only=True)
    write_tool = f"mcp__{SERVER_ID}__send_message"
    assert capabilities_for_tool(write_tool).known is False
    assert _armed_context().decision_for(write_tool, "{}").allowed is False


def test_default_declaration_is_still_fail_high():
    # read_only=False must leave the tool unclassified.
    declare_mcp_tool_capabilities(QUALIFIED, read_only=False)
    assert capabilities_for_tool(QUALIFIED).known is False


def test_read_private_is_in_the_post_external_block_set():
    # Pin the interaction this file exists to document: a declared read-only
    # OXS tool is READ_PRIVATE, and READ_PRIVATE is blocked post-external
    # once a *non*-control-plane source has landed in context.
    assert ToolEffect.READ_PRIVATE in POST_EXTERNAL_BLOCKED_EFFECTS

    declare_mcp_tool_capabilities(QUALIFIED, read_only=True)
    # Control-plane sources only → the read-only allowance applies.
    control_plane = _armed_context()
    assert control_plane.decision_for(QUALIFIED, "{}").allowed is True

    # A real tool result has landed → the allowance no longer applies.
    after_result = _armed_context(QUALIFIED)
    assert after_result.decision_for(QUALIFIED, "{}").allowed is False


def test_declared_task_tools_unblock_background_reads():
    declare_mcp_tool_capabilities(QUALIFIED, read_only=True)
    ctx = _armed_context(QUALIFIED)
    ctx.declared_task_tools = frozenset({QUALIFIED})
    assert ctx.decision_for(QUALIFIED, "{}").allowed is True

    # Scope is exact: a tool outside the declared set is still refused.
    other = f"mcp__{SERVER_ID}__get_message"
    assert ctx.decision_for(other, "{}").allowed is False


def test_declared_task_tools_never_lift_a_delegated_credential():
    declare_mcp_tool_capabilities(QUALIFIED, read_only=True)
    ctx = _armed_context(QUALIFIED)
    ctx.declared_task_tools = frozenset({QUALIFIED})
    ctx.delegated_credential = True
    assert ctx.decision_for(QUALIFIED, "{}").allowed is False


def test_background_stream_passes_declared_scope():
    """``stream_agent_loop`` only populates ``declared_task_tools`` for
    background workloads that were given an explicit server-side tool set."""
    src = (REPO / "src" / "agent_loop.py").read_text()
    assert 'workload == "background"' in src
    assert "declared_task_tools=" in src
    # Foreground chat must not reach the same branch.
    assert (
        "workload == \"background\"\n                and relevant_tools"
        in src
        or 'workload == "background"\n                and relevant_tools' in src
    )


def test_extension_declares_every_bridge_tool_read_only():
    ext = REPO.parent / "odysseus-w2a" / "extensions" / "whats2api_bridge"
    plugin = ext / "plugin.py"
    if not plugin.exists():
        pytest.skip("whats2api_bridge extension not present next to the core repo")
    text = plugin.read_text()
    assert "read_only=True" in text, (
        "the bridge must declare its MCP tools read_only so the approval gate "
        "admits them for the scheduled analyzer"
    )
