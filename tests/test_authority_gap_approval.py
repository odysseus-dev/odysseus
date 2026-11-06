"""An ungranted call asks the user for that exact action instead of failing (#6631)."""
import json
from collections import namedtuple
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.agent_runtime.authority import (
    ExactOperation, OperationGrant, RequestAuthority, approval_can_admit, create_request_authority,
)
from src.tool_approval_scopes import ToolApprovalScope
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_policy import ToolPolicy


ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
MCP_TOOL = "mcp__d40e072b__jev_classify"


def _authority(*tools, owner="alice", session_id="s", workspace=""):
    return RequestAuthority("gap-test-request", owner, session_id, workspace,
                            tuple(OperationGrant(t) for t in ("ask_user", *tools)))


def _admit(authority, tool, content="{}", **kwargs):
    context = {"owner": "alice", "session_id": "s", "workspace": "", **kwargs}
    return approval_can_admit(authority, tool, content, **context)


def test_only_a_missing_grant_is_approvable():
    authority = _authority("web_search")
    assert _admit(authority, MCP_TOOL)
    assert _admit(authority, "bash", "pwd")
    assert not _admit(authority, "web_search", "news")


@pytest.mark.parametrize("change", ["child", "owner", "session", "disabled", "disable_mcp", "block_all", "malformed"])
def test_hard_limits_are_never_approvable(change):
    authority = _authority()
    kwargs = {}
    content = "{}"
    if change == "child":
        authority = authority.continuation(owner="alice", session_id="s")
    elif change == "owner":
        kwargs["owner"] = "bob"
    elif change == "session":
        kwargs["session_id"] = "other"
    elif change == "disabled":
        kwargs["disabled_tools"] = {MCP_TOOL}
    elif change == "disable_mcp":
        kwargs["policy"] = ToolPolicy(disable_mcp=True)
    elif change == "block_all":
        kwargs["policy"] = ToolPolicy(block_all_tool_calls=True)
    else:
        content = '{"a": 1, "a": 2}'
    assert not _admit(authority, MCP_TOOL, content, **kwargs)


def test_issue_repro_custom_mcp_tool_is_approvable_not_dead_end():
    authority = create_request_authority("Use the jev_classify tool to classify my recent emails", owner="alice",
                                         session_id="s")
    assert not authority.permits(ExactOperation.normalize(MCP_TOOL, "{}"))
    assert _admit(authority, MCP_TOOL)


def _gap_pending(store, *, tool="web_search", content="latest news", tainted=False, authority=None):
    return store.create(
        owner="alice", session_id="s", origin_run_id="run-1", tool_name=tool, content=content,
        workspace=None, external_untrusted_context_seen=tainted,
        capabilities=capabilities_for_action(tool, content),
        request_authority=authority or _authority(), authority_gap=True)


def test_gap_card_offers_only_this_action_or_deny():
    payload = _gap_pending(ToolApprovalStore()).public_payload(reason="Not covered")
    assert payload["kind"] == "tool_approval"
    assert payload["question"] == "Allow this action?"
    assert payload["description"] == "Not covered"
    assert [o["label"] for o in payload["options"]] == ["Allow this action", "Deny"]
    assert payload["action"]["tool"] == "web_search"
    assert payload["action"]["content"] == "latest news"


@pytest.mark.parametrize("decision", ["approve_task", "approve"])
def test_gap_approval_is_always_one_action(decision):
    store = ToolApprovalStore()
    pending = _gap_pending(store)
    grant = store.consume(pending.approval_id, decision=decision, owner="alice", session_id="s")
    assert grant.scope is ToolApprovalScope.SINGLE_ACTION
    assert not grant.allow_remaining_actions and not grant.grants_chat_session


def test_gap_flag_is_digest_bound():
    store = ToolApprovalStore()
    pending = _gap_pending(store)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    assert grant.matches(owner="alice", session_id="s", tool_name="web_search", content="latest news", workspace=None)
    forged = replace(grant, pending=replace(grant.pending, authority_gap=False))
    assert not forged.matches(owner="alice", session_id="s", tool_name="web_search", content="latest news",
                              workspace=None)


async def _dispatch(monkeypatch, grant, *, security_context, authority):
    from src import tool_execution
    implementation = AsyncMock(return_value=("web_search", {"output": "ok", "exit_code": 0}))
    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", implementation)
    result = await tool_execution.execute_tool_block(
        ToolBlock("web_search", "latest news"), owner="alice", session_id="s", workspace=None,
        security_context=security_context, exact_approval=grant, request_authority=authority)
    return implementation, result


@pytest.mark.asyncio
async def test_dispatcher_runs_approved_gap_without_untrusted_context(monkeypatch):
    store = ToolApprovalStore()
    authority = _authority()
    pending = _gap_pending(store, authority=authority)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    implementation, (_, result) = await _dispatch(monkeypatch, grant, security_context=ToolRunSecurityContext(),
                                                  authority=authority)
    implementation.assert_awaited_once()
    assert result["exit_code"] == 0
    assert not grant.matches(owner="alice", session_id="s", tool_name="web_search", content="latest news",
                             workspace=None), "approval must be claimed once"


@pytest.mark.asyncio
async def test_dispatcher_rejects_gap_card_shown_before_run_became_tainted(monkeypatch):
    store = ToolApprovalStore()
    authority = _authority()
    pending = _gap_pending(store, authority=authority, tainted=False)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    implementation, (_, result) = await _dispatch(
        monkeypatch, grant, security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
        authority=authority)
    implementation.assert_not_awaited()
    assert result["blocked"] is True and result["policy"] == "exact_tool_approval"


@pytest.mark.asyncio
async def test_non_gap_approval_still_requires_armed_context(monkeypatch):
    store = ToolApprovalStore()
    authority = _authority()
    pending = store.create(
        owner="alice", session_id="s", origin_run_id="run-1", tool_name="web_search", content="latest news",
        workspace=None, external_untrusted_context_seen=False,
        capabilities=capabilities_for_action("web_search", "latest news"), request_authority=authority)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    implementation, (_, result) = await _dispatch(monkeypatch, grant, security_context=ToolRunSecurityContext(),
                                                  authority=authority)
    implementation.assert_not_awaited()
    assert result["blocked"] is True


def _patch_loop(monkeypatch, responses, implementation):
    from src import agent_loop as loop, tool_execution as execution
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    monkeypatch.setattr(loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(loop, "estimate_tokens", lambda *a, **k: 10)
    monkeypatch.setattr(loop, "blocked_tools_for_owner", lambda owner: set(), raising=False)
    calls = iter(responses)
    model_rounds = []

    async def fake_stream(*args, **kwargs):
        model_rounds.append(kwargs)
        yield "data: " + json.dumps({"delta": next(calls, "Done.")}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(loop, "stream_llm_with_fallback", fake_stream)
    return loop, model_rounds


async def _run(loop, authority, **kwargs):
    events = []
    async for chunk in loop.stream_agent_loop(
            "https://inference.invalid", "test", [{"role": "user", "content": "Search the web for the latest news"}],
            owner="alice", session_id="s", max_rounds=kwargs.pop("max_rounds", 2),
            relevant_tools={"web_search"}, request_authority=authority, **kwargs):
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                events.append(json.loads(chunk[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _outputs(events, tool="web_search"):
    return [e for e in events if e.get("type") == "tool_output" and e.get("tool") == tool]


@pytest.mark.asyncio
async def test_loop_asks_for_ungranted_call_when_a_person_can_answer(monkeypatch):
    implementation = AsyncMock(return_value=("web_search", {"output": "unexpected", "exit_code": 0}))
    loop, _ = _patch_loop(monkeypatch, ["```web_search\nlatest news\n```"], implementation)
    events = await _run(loop, _authority(), authority_gap_approvals=True)
    implementation.assert_not_awaited()
    cards = [e["ask_user"] for e in _outputs(events) if e.get("ask_user")]
    assert cards and cards[0]["question"] == "Allow this action?"
    from src.tool_approvals import tool_approval_store
    assert tool_approval_store.peek(cards[0]["approval_id"]).authority_gap


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["default", "delegated", "child"])
async def test_loop_keeps_denial_when_nobody_can_approve(monkeypatch, variant):
    implementation = AsyncMock(return_value=("web_search", {"output": "unexpected", "exit_code": 0}))
    loop, _ = _patch_loop(monkeypatch, ["```web_search\nlatest news\n```"], implementation)
    authority = _authority()
    kwargs = {}
    if variant == "delegated":
        kwargs = {"authority_gap_approvals": True, "delegated_credential": True}
    elif variant == "child":
        authority = authority.continuation(owner="alice", session_id="s")
        kwargs = {"authority_gap_approvals": True}
    events = await _run(loop, authority, **kwargs)
    implementation.assert_not_awaited()
    outputs = _outputs(events)
    assert outputs and not any(e.get("ask_user") for e in outputs)


@pytest.mark.asyncio
async def test_loop_does_not_ask_for_policy_denied_call(monkeypatch):
    implementation = AsyncMock(return_value=("web_search", {"output": "unexpected", "exit_code": 0}))
    loop, _ = _patch_loop(monkeypatch, ["```web_search\nlatest news\n```"], implementation)
    events = await _run(loop, _authority(), authority_gap_approvals=True, disabled_tools={"web_search"})
    implementation.assert_not_awaited()
    assert not any(e.get("ask_user") for e in events if e.get("type") == "tool_output")


@pytest.mark.asyncio
async def test_approved_gap_runs_once_and_later_ungranted_call_asks_again(monkeypatch):
    implementation = AsyncMock(return_value=("web_search", {"output": "Result A", "exit_code": 0}))
    loop, _ = _patch_loop(monkeypatch, ["```web_search\nother query\n```"], implementation)
    store = loop.tool_approval_store
    authority = _authority()
    pending = _gap_pending(store, authority=authority)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    events = await _run(loop, authority, exact_approval=grant, authority_gap_approvals=True, max_rounds=2)
    assert implementation.await_count == 1
    assert implementation.await_args.args[0].content == "latest news"
    second = [e for e in _outputs(events) if e.get("ask_user")]
    assert second and second[0]["ask_user"]["action"]["content"] == "other query"


@pytest.mark.asyncio
async def test_failed_approved_action_returns_to_model(monkeypatch):
    implementation = AsyncMock(return_value=("web_search", {"error": "HTTP 403", "exit_code": 1}))
    loop, model_rounds = _patch_loop(monkeypatch, ["The site refused; here is another source."], implementation)
    store = loop.tool_approval_store
    authority = _authority()
    pending = _gap_pending(store, authority=authority)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    events = await _run(loop, authority, exact_approval=grant, authority_gap_approvals=True, max_rounds=2)
    implementation.assert_awaited_once()
    assert model_rounds, "the model must see the failure and continue"
    finals = [e.get("content", "") for e in events if e.get("type") == "final_response"]
    assert not any("could not run" in text for text in finals)


@pytest.mark.asyncio
async def test_rejected_approved_action_still_ends_turn(monkeypatch):
    implementation = AsyncMock(return_value=("web_search", {"error": "Tool disabled", "exit_code": 1,
                                                            "blocked": True}))
    loop, model_rounds = _patch_loop(monkeypatch, ["unexpected"], implementation)
    store = loop.tool_approval_store
    authority = _authority()
    pending = _gap_pending(store, authority=authority)
    grant = store.consume(pending.approval_id, decision="approve_task", owner="alice", session_id="s")
    events = await _run(loop, authority, exact_approval=grant, authority_gap_approvals=True, max_rounds=2)
    assert not model_rounds
    finals = [e.get("content", "") for e in events if e.get("type") == "final_response"]
    assert any("could not run" in text for text in finals)
