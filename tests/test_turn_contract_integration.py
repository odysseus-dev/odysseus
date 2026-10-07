"""Exercise contract enforcement through the real generator and dispatcher."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from tests.runtime_evidence_helpers import server_authorized_executor


@pytest.fixture(autouse=True)
def standalone_dispatch_authority(monkeypatch):
    from src import tool_execution
    monkeypatch.setattr(tool_execution, "execute_tool_block",
                        server_authorized_executor(tool_execution.execute_tool_block))

from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_types import ToolBlock
from src.turn_contract import (
    active_turn_contract, bind_turn_contract, resolve_turn_contract, with_turn_contract,
)


def contract(capabilities, **kwargs):
    return resolve_turn_contract(
        capabilities=capabilities, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=kwargs.pop("policy", ToolPolicy()), **kwargs,
    )


@pytest.fixture
def forbidden_inference_and_execution(monkeypatch):
    from src import agent_loop, tool_execution

    spies = []
    for module, name in (
        (agent_loop, "stream_llm"),
        (agent_loop, "stream_llm_with_fallback"),
        (agent_loop, "execute_tool_block"),
        (tool_execution, "execute_tool_block"),
    ):
        spy = Mock(side_effect=AssertionError(f"Unexpected call: {name}"))
        monkeypatch.setattr(module, name, spy)
        spies.append(spy)
    yield
    for spy in spies:
        spy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["calendar", "catalog"])
async def test_actual_generator_reports_unavailable_before_inference(
    case, forbidden_inference_and_execution,
):
    from src.agent_loop import stream_agent_loop

    if case == "calendar":
        selected = contract({"calendar", "search_browser"}, policy=ToolPolicy(
            disabled_tools=frozenset({"manage_calendar"})))
        missing = "manage_calendar"
    elif case == "catalog":
        selected = contract({"cookbook_admin"}, required_tools={"list_models"},
                            policy=ToolPolicy(disabled_tools=frozenset({"list_models"})))
        missing = "list_models"
    chunks = [chunk async for chunk in stream_agent_loop(
        "https://inference.invalid", "unused-model",
        [{"role": "user", "content": "Perform the requested action"}],
        turn_contract=selected,
        # Recovery hints and candidates must not bypass the initial guard.
        forced_tools={"web_search"}, relevant_tools={"web_search"},
        fallbacks=[("https://fallback.invalid", "unused-fallback", {})],
    )]
    assert len(chunks) == 4
    assert json.loads(chunks[0].removeprefix("data: ")) == {
        "type": "turn_contract", **selected.audit(),
    }
    decision = json.loads(chunks[1].removeprefix("data: "))
    assert decision["type"] == "completion_decision"
    assert decision["data"]["status"] == "unverified"
    assert decision["data"]["evidence_ids"] == []
    failure = json.loads(chunks[2].removeprefix("data: "))
    assert set(failure) == {"delta"}
    assert "can’t perform" in failure["delta"]
    assert "unavailable" in failure["delta"]
    assert missing in failure["delta"]
    assert "haven’t substituted another tool" in failure["delta"]
    assert chunks[3] == "data: [DONE]\n\n"


def test_unknown_contract_reaches_model_instead_of_forced_clarification():
    from src.agent_loop import _blocks_before_inference

    assert not _blocks_before_inference(contract({"unknown"}))
    assert _blocks_before_inference(contract(
        {"calendar"},
        policy=ToolPolicy(disabled_tools=frozenset({"manage_calendar"})),
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("use_bridge", [False, True])
async def test_stream_bridge_binds_contract_for_actual_generator_and_resets(
    use_bridge, forbidden_inference_and_execution,
):
    from routes.chat_routes import _stream_agent_with_execution_bridge
    from src.tool_execution import (
        AgentExecutionBridge, bind_execution_bridge, get_active_execution_bridge,
    )

    transport = AsyncMock(side_effect=AssertionError("External transport called"))
    outer_bridge = AgentExecutionBridge(transport, frozenset({"bash"}), name="outer")
    inner_bridge = AgentExecutionBridge(transport, frozenset({"web_search"}), name="inner")
    outer = contract({"notes"})
    # Use an actually unavailable capability to stop before inference.
    # Unknown intent is deliberately allowed to reach the model.
    selected = contract({"calendar"}, policy=ToolPolicy(disabled_tools=frozenset({"manage_calendar"})))
    previous = active_turn_contract(), get_active_execution_bridge()
    with bind_turn_contract(outer), bind_execution_bridge(outer_bridge):
        chunks = []
        async for chunk in _stream_agent_with_execution_bridge(
            inner_bridge if use_bridge else None,
            "https://inference.invalid", "unused", [], turn_contract=selected,
        ):
            assert active_turn_contract() is selected
            assert get_active_execution_bridge() is (inner_bridge if use_bridge else outer_bridge)
            chunks.append(chunk)
        assert active_turn_contract() is outer
        assert get_active_execution_bridge() is outer_bridge
    assert (active_turn_contract(), get_active_execution_bridge()) == previous
    assert chunks[-1] == "data: [DONE]\n\n"
    transport.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["close", "exception", "cancel"])
@pytest.mark.parametrize("use_bridge", [False, True])
async def test_stream_bridge_resets_binding_on_abnormal_exit(monkeypatch, ending, use_bridge):
    from routes import chat_routes
    from src.tool_execution import (
        AgentExecutionBridge, bind_execution_bridge, get_active_execution_bridge,
    )

    transport = AsyncMock(side_effect=AssertionError("External transport called"))
    outer_bridge = AgentExecutionBridge(transport, frozenset({"bash"}), name="outer")
    inner_bridge = AgentExecutionBridge(transport, frozenset({"bash"}), name="inner")
    outer, selected = contract({"notes"}), contract({"calendar"})

    async def controlled_stream(*args, **kwargs):
        assert kwargs["turn_contract"] is selected
        assert active_turn_contract() is selected
        assert get_active_execution_bridge() is (inner_bridge if use_bridge else outer_bridge)
        yield "first chunk"
        if ending == "exception":
            raise RuntimeError("stream failed")
        if ending == "cancel":
            raise asyncio.CancelledError()

    monkeypatch.setattr(chat_routes, "stream_agent_loop", controlled_stream)
    previous = active_turn_contract(), get_active_execution_bridge()
    with bind_turn_contract(outer), bind_execution_bridge(outer_bridge):
        stream = chat_routes._stream_agent_with_execution_bridge(
            inner_bridge if use_bridge else None, turn_contract=selected,
        )
        try:
            assert await anext(stream) == "first chunk"
            if ending == "close":
                await stream.aclose()
            else:
                exception = RuntimeError if ending == "exception" else asyncio.CancelledError
                with pytest.raises(exception):
                    await anext(stream)
            assert active_turn_contract() is outer
            assert get_active_execution_bridge() is outer_bridge
        finally:
            await stream.aclose()
    assert (active_turn_contract(), get_active_execution_bridge()) == previous
    transport.assert_not_called()


@pytest.mark.asyncio
async def test_selected_contract_does_not_bypass_owner_guard(monkeypatch):
    from src import tool_execution, tool_implementations

    selected = contract({"cookbook_admin"})
    assert selected.permits("download_model")
    handler = AsyncMock(side_effect=AssertionError("Download executed"))
    monkeypatch.setattr(tool_implementations, "do_download_model", handler)
    # Stub identity lookup, retaining the real dispatcher's admin guard.
    identity = Mock(return_value=False)
    monkeypatch.setattr(tool_execution, "owner_is_admin_or_single_user", identity)
    with bind_turn_contract(selected):
        description, result = await tool_execution.execute_tool_block(
            ToolBlock("download_model", '{"repo_id":"example/model"}'),
            owner="non-admin", security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        )
    assert "BLOCKED" in description
    assert result["exit_code"] == 1
    assert "requires an admin" in result["error"]
    identity.assert_called_with("non-admin")
    handler.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatched_approval", [False, True])
async def test_selected_contract_does_not_bypass_approval_guard(monkeypatch, mismatched_approval):
    from src import tool_capabilities, tool_execution
    from src.tool_approvals import ToolApprovalStore
    from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action

    selected = contract({"shell_files"})
    assert selected.permits("bash")
    # Exercise the gate explicitly, independent of deployment configuration.
    monkeypatch.setattr(tool_capabilities, "TOOL_APPROVAL_GATE_ENABLED", True)
    implementation = AsyncMock(side_effect=AssertionError("Shell execution reached"))
    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", implementation)
    grant = None
    if mismatched_approval:
        store = ToolApprovalStore()
        pending = store.create(
            owner="alice", session_id="test-session", origin_run_id="test-run",
            tool_name="bash", content="printf approved", workspace=None,
            external_untrusted_context_seen=True,
            capabilities=capabilities_for_action("bash", "printf approved"),
        )
        grant = store.consume(pending.approval_id, decision="approve",
                              owner="alice", session_id="test-session")
        assert grant is not None
    with bind_turn_contract(selected):
        description, result = await tool_execution.execute_tool_block(
            ToolBlock("bash", "printf unapproved > /tmp/turn-contract-unapproved"),
            owner="alice", session_id="test-session",
            security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
            exact_approval=grant,
        )
    assert "BLOCKED" in description
    assert result["blocked"] is True
    assert result["exit_code"] == 1
    assert result["policy"] == (
        "exact_tool_approval" if mismatched_approval else "external_untrusted_context"
    )
    implementation.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_early", [False, True])
async def test_direct_agent_caller_binds_contract_and_restores_context(
    close_early, forbidden_inference_and_execution,
):
    from src.agent_loop import stream_agent_loop

    outer = contract({"notes"})
    selected = contract({"calendar"}, policy=ToolPolicy(disabled_tools=frozenset({"manage_calendar"})))
    previous = active_turn_contract()
    with bind_turn_contract(outer):
        stream = stream_agent_loop(
            "https://inference.invalid", "unused", [], turn_contract=selected,
        )
        assert active_turn_contract() is outer  # Binding begins on iteration.
        try:
            first = await anext(stream)
            assert json.loads(first.removeprefix("data: "))["type"] == "turn_contract"
            assert active_turn_contract() is selected
            if close_early:
                await stream.aclose()
            else:
                remaining = [chunk async for chunk in stream]
                assert remaining[-1] == "data: [DONE]\n\n"
            assert active_turn_contract() is outer
        finally:
            await stream.aclose()
    assert active_turn_contract() is previous


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["complete", "close", "exception", "cancel"])
async def test_asyncgen_decorator_closes_inner_stream_before_restoring(ending):
    import inspect

    selected, outer = contract({"calendar"}), contract({"notes"})
    cleaned = []

    async def source(turn_contract=None):
        """A stream with cleanup that depends on its contract."""
        try:
            assert active_turn_contract() is turn_contract
            yield "chunk"
            if ending == "exception":
                raise RuntimeError("stream failure")
            if ending == "cancel":
                raise asyncio.CancelledError()
        finally:
            cleaned.append(active_turn_contract())

    decorated = with_turn_contract(source)
    assert decorated.__name__ == source.__name__
    assert decorated.__doc__ == source.__doc__
    assert inspect.signature(decorated) == inspect.signature(source)
    with bind_turn_contract(outer):
        stream = decorated(selected)  # Positional contract also binds.
        try:
            assert await anext(stream) == "chunk"
            if ending == "close":
                await stream.aclose()
            else:
                expected = {"complete": StopAsyncIteration, "exception": RuntimeError,
                            "cancel": asyncio.CancelledError}[ending]
                with pytest.raises(expected):
                    await anext(stream)
            assert cleaned == [selected]
            assert active_turn_contract() is outer
        finally:
            await stream.aclose()


@pytest.mark.asyncio
async def test_asyncgen_decorator_omitted_contract_does_not_inherit_other_turn():
    @with_turn_contract
    async def source(turn_contract=None):
        assert active_turn_contract() is None
        yield "chunk"

    outer = contract({"calendar"})
    with bind_turn_contract(outer):
        assert [chunk async for chunk in source()] == ["chunk"]
        assert active_turn_contract() is outer


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["disabled_tools", "policy", "hidden", "all"])
async def test_runtime_denial_still_blocks_tool_selected_by_contract(monkeypatch, denial):
    from src import tool_execution, tool_implementations

    selected = contract({"calendar"})
    assert selected.permits("manage_calendar")
    handler = AsyncMock(side_effect=AssertionError("Denied calendar tool executed"))
    monkeypatch.setattr(tool_implementations, "do_manage_calendar", handler)
    runtime = {
        "disabled_tools": {"disabled_tools": {"manage_calendar"}},
        "policy": {"tool_policy": ToolPolicy(disabled_tools=frozenset({"manage_calendar"}))},
        "hidden": {"tool_policy": ToolPolicy(hidden_tools=frozenset({"manage_calendar"}))},
        "all": {"tool_policy": ToolPolicy(block_all_tool_calls=True)},
    }[denial]
    with bind_turn_contract(selected):
        description, result = await tool_execution.execute_tool_block(
            ToolBlock("manage_calendar", '{"action":"list"}'),
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT, **runtime,
        )
    assert "BLOCKED" in description
    assert result["exit_code"] == 1
    assert "disabled" in result["error"] or "policy" in result["error"]
    assert result.get("failure_kind") != "turn_contract_denied"
    handler.assert_not_called()
