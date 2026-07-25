import time
from collections import namedtuple

import pytest

from src.agent_run_policy import (
    AgentRunMode,
    AgentRunPolicy,
    AuthorizationOutcome,
    ExecutionProfile,
    parse_agent_run_mode,
)
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_tool


def test_invalid_run_mode_fails_safe_to_sandbox():
    assert parse_agent_run_mode("made-up") is AgentRunMode.SANDBOX
    assert AgentRunPolicy.for_mode(None).mode is AgentRunMode.SANDBOX


def test_ask_requires_exact_approval_for_code_but_not_public_read():
    policy = AgentRunPolicy.for_mode("ask")
    context = ToolRunSecurityContext()

    assert policy.authorize("web_search", context).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    assert policy.authorize("bash", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_sandbox_allows_code_before_external_context_then_requires_approval():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext()

    assert policy.authorize("bash", context).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    context.external_untrusted_context_seen = True
    assert policy.authorize("bash", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_sandbox_requires_approval_for_external_side_effects():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext()

    assert policy.authorize("send_email", context).outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def test_unknown_tool_requires_approval_outside_full_access():
    context = ToolRunSecurityContext()

    assert AgentRunPolicy.for_mode("sandbox").authorize(
        "mcp__unknown__surprise", context
    ).outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert AgentRunPolicy.for_mode("full_access").authorize(
        "mcp__unknown__surprise", context
    ).outcome is AuthorizationOutcome.ALLOW_HOST


def test_full_access_selects_host_execution_profile():
    policy = AgentRunPolicy.for_mode("full_access")

    assert policy.execution_profile is ExecutionProfile.HOST_FULL_ACCESS
    assert policy.authorize(
        "bash", ToolRunSecurityContext(external_untrusted_context_seen=True)
    ).outcome is AuthorizationOutcome.ALLOW_HOST


def _pending(store, **overrides):
    values = {
        "owner": "Alice",
        "session_id": "session-1",
        "origin_run_id": "run-1",
        "tool_name": "bash",
        "content": "printf exact",
        "workspace": "/tmp/workspace",
        "security_mode": "ask",
        "external_untrusted_context_seen": True,
        "capabilities": capabilities_for_tool("bash"),
    }
    values.update(overrides)
    return store.create(**values)


def test_approval_is_bound_to_exact_action_and_claimed_once():
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf modified",
        workspace="/tmp/workspace",
        security_mode="ask",
    )
    assert grant.claim(
        owner="ALICE",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace="/tmp/workspace",
        security_mode="ask",
    )
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace="/tmp/workspace",
        security_mode="ask",
    )


def test_approval_wrong_owner_is_destroyed_without_grant():
    store = ToolApprovalStore()
    pending = _pending(store)

    assert store.consume(
        pending.approval_id,
        decision="approve",
        owner="mallory",
        session_id="session-1",
    ) is None
    assert store.peek(pending.approval_id) is None


def test_deny_destructively_consumes_pending_action():
    store = ToolApprovalStore()
    pending = _pending(store)

    assert store.consume(
        pending.approval_id,
        decision="deny",
        owner="alice",
        session_id="session-1",
    ) is None
    assert store.peek(pending.approval_id) is None


def test_expired_approval_cannot_be_consumed(monkeypatch):
    store = ToolApprovalStore(ttl_seconds=1)
    pending = _pending(store)
    monkeypatch.setattr(time, "time", lambda: pending.expires_at + 1)

    assert store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    ) is None


def test_public_approval_payload_shows_the_complete_exact_action():
    store = ToolApprovalStore()
    pending = _pending(store, content="printf safe\nSECRET_SECOND_LINE")

    payload = pending.public_payload()
    encoded = str(payload)
    assert payload["kind"] == "tool_approval"
    assert payload["action"]["content"] == "printf safe\nSECRET_SECOND_LINE"
    assert "SECRET_SECOND_LINE" in encoded


@pytest.mark.asyncio
async def test_dispatcher_claims_exact_approval_immediately_before_execution(
    monkeypatch,
):
    import src.tool_execution as tool_execution

    ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )
    calls = []

    async def fake_implementation(block, **kwargs):
        calls.append((block.tool_type, block.content, kwargs["execution_profile"]))
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        fake_implementation,
    )
    desc, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf exact"),
        session_id="session-1",
        owner="alice",
        workspace="/tmp/workspace",
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        run_policy=AgentRunPolicy.for_mode("ask"),
        exact_approval=grant,
    )

    assert desc == "bash"
    assert result["exit_code"] == 0
    assert calls == [
        ("bash", "printf exact", ExecutionProfile.WORKSPACE_SANDBOX)
    ]


@pytest.mark.asyncio
async def test_dispatcher_rejects_modified_action_without_execution(monkeypatch):
    import src.tool_execution as tool_execution

    ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("modified approved action reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf changed"),
        session_id="session-1",
        owner="alice",
        workspace="/tmp/workspace",
        security_context=ToolRunSecurityContext(),
        run_policy=AgentRunPolicy.for_mode("ask"),
        exact_approval=grant,
    )

    assert result["blocked"] is True
    assert result["policy"] == "exact_tool_approval"


@pytest.mark.asyncio
async def test_dispatcher_rejects_full_access_for_non_admin(monkeypatch):
    import src.tool_execution as tool_execution

    ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    monkeypatch.setattr(
        tool_execution,
        "owner_is_admin_or_single_user",
        lambda owner: False,
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("non-admin host action reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf host"),
        session_id="session-1",
        owner="ordinary-user",
        workspace="/tmp/workspace",
        security_context=ToolRunSecurityContext(),
        run_policy=AgentRunPolicy.for_mode("full_access"),
    )

    assert result["blocked"] is True
    assert "admin" in result["error"].lower()
