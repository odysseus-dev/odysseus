"""The Spec router may select a tool the request policy has withheld."""

import json
import sys
import types
from dataclasses import replace

import pytest

from src import agent_loop
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_turn_contract


PROMPT = "open twitter and see what elon tweeted last"


@pytest.fixture(autouse=True)
def isolate_active_contract():
    from src.turn_contract import _ACTIVE_CONTRACT
    token = _ACTIVE_CONTRACT.set(None)
    yield
    _ACTIVE_CONTRACT.reset(token)


def _route(monkeypatch, disabled, *, subset="1", base_browser=True, hidden=(),
           mode="strict", inherited_unavailable=()):
    fake = types.ModuleType("src.spec_router")
    fake.LAST_DECISIONS = [{
        "choice": "t62", "top": [("private_browser", 0.859)],
    }]
    fake.latest_decision = lambda: fake.LAST_DECISIONS[-1]
    fake.spec_route_tools = lambda *a, **kw: ({"private_browser"}, True)
    monkeypatch.setitem(sys.modules, "src.spec_router", fake)
    monkeypatch.setattr(agent_loop, "_configured_model_tool_surface_raw", lambda *a, **kw: "spec")
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setenv("ODYSSEUS_SPEC_MODE", mode)
    monkeypatch.setenv("ODYSSEUS_SPEC_SUBSET_OF_BASE", subset)

    # Full schema inventory and an actual request policy are present. The
    # positive case can execute the browser; the negative case cannot.
    policy = ToolPolicy(disabled_tools=frozenset(disabled), hidden_tools=frozenset(hidden))
    contract = resolve_turn_contract(
        capabilities={"search_browser"}, required_capabilities=set(),
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=policy,
        selected_tools={"private_browser"} if base_browser else {"ask_user"},
        message=PROMPT,
    )
    if inherited_unavailable:
        contract = replace(contract, unavailable=frozenset(inherited_unavailable))
    return agent_loop._spec_router_contract(
        contract, [{"role": "user", "content": PROMPT}],
        set(disabled), "http://fixture", "Ajax", "fixture", {}, tool_policy=policy,
    )


@pytest.mark.parametrize("subset", ["0", "1"])
def test_spec_browser_withheld_by_web_policy_never_reaches_model(monkeypatch, subset):
    routed = _route(monkeypatch, {"private_browser"}, subset=subset)
    assert routed.routing_experiment == "spec-policy-denied"
    assert routed.unavailable == frozenset({"private_browser"})
    assert routed.offered == frozenset()
    assert routed.schema_json == ()
    assert "Enable Web search" in agent_loop._spec_unavailable_reply(routed.unavailable)
    assert "have not opened" in agent_loop._spec_unavailable_reply(routed.unavailable)


@pytest.mark.asyncio
async def test_spec_unavailable_contract_finishes_before_provider_call(monkeypatch):
    routed = _route(monkeypatch, {"private_browser"})

    async def unexpected_provider_path(*args, **kwargs):
        raise AssertionError("the provider/runtime must not see a denied tool")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", unexpected_provider_path)
    events = []
    async for chunk in agent_loop.stream_agent_loop(
        endpoint_url="http://fixture", model="Ajax",
        messages=[{"role": "user", "content": PROMPT}],
        turn_contract=routed, disabled_tools={"private_browser"},
        tool_policy=ToolPolicy(disabled_tools=frozenset({"private_browser"})),
        _is_teacher_run=True,
    ):
        events.append(chunk)
    parsed = [json.loads(chunk[6:]) for chunk in events if chunk.startswith("data: {")]
    assert any(event.get("type") == "turn_contract" for event in parsed)
    assert any("Enable Web search" in event.get("delta", "") for event in parsed)
    assert not any(event.get("type") == "tool_start" for event in parsed)
    assert events[-1] == "data: [DONE]\n\n"


@pytest.mark.parametrize("subset,base_browser", [("0", True), ("1", True)])
def test_spec_browser_allowed_keeps_executable_tool(monkeypatch, subset, base_browser):
    routed = _route(monkeypatch, set(), subset=subset, base_browser=base_browser)
    assert routed.routing_experiment == "spec-router"
    assert not routed.unavailable
    assert "private_browser" in routed.offered
    assert "private_browser" in routed.executable
    assert any(json.loads(schema)["function"]["name"] == "private_browser"
               for schema in routed.schema_json)


def test_subset_control_does_not_confuse_base_omission_with_policy_denial(monkeypatch):
    routed = _route(monkeypatch, set(), subset="1", base_browser=False)
    assert not routed.unavailable
    assert "private_browser" not in routed.offered
    assert "private_browser" in routed.executable


def test_subset_override_cannot_add_an_unoffered_browser(monkeypatch):
    routed = _route(monkeypatch, set(), subset="0", base_browser=False)
    assert "private_browser" not in routed.offered


@pytest.mark.asyncio
async def test_non_strict_inherited_unavailable_continues_to_preview(monkeypatch):
    # Non-strict Spec preserves an unrelated base-contract unavailable value.
    # That value must not be mistaken for our newly sealed policy conflict.
    inherited = _route(monkeypatch, set(), subset="0", mode="additive",
                       inherited_unavailable={"manage_notes"})
    assert inherited.routing_experiment != "spec-router"
    assert inherited.unavailable == frozenset({"manage_notes"})

    async def fake_preview(**kwargs):
        yield 'data: {"delta": "preview continued"}\n\n'
        yield 'data: [DONE]\n\n'

    monkeypatch.setattr("src.clean_agent_preview.stream_preview", fake_preview)
    monkeypatch.setattr(agent_loop, "is_compact_preview_contract", lambda contract: True)
    events = [chunk async for chunk in agent_loop.stream_agent_loop(
        endpoint_url="http://fixture", model="Ajax",
        messages=[{"role": "user", "content": PROMPT}],
        turn_contract=inherited, disabled_tools=set(), tool_policy=ToolPolicy(),
        _is_teacher_run=True,
    )]
    assert not any("preview continued" in chunk for chunk in events)
    assert any("manage_notes" in chunk for chunk in events)


def test_policy_hidden_browser_is_denied_even_without_disabled_snapshot(monkeypatch):
    routed = _route(monkeypatch, set(), subset="0", hidden={"private_browser"})
    assert routed.unavailable == frozenset({"private_browser"})
    assert not routed.offered


def test_guide_only_policy_keeps_its_existing_control_path(monkeypatch):
    monkeypatch.setattr(agent_loop, "_configured_model_tool_surface_raw", lambda *a, **kw: "spec")
    contract = resolve_turn_contract(
        capabilities=set(), required_capabilities=set(), schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(), message="guide me only",
    )
    routed = agent_loop._spec_router_contract(
        contract, [{"role": "user", "content": "guide me only"}],
        set(), "http://fixture", "Ajax", "fixture", {},
        tool_policy=ToolPolicy(block_all_tool_calls=True, mode="guide_only"),
    )
    assert routed is contract


def test_spec_direct_answer_is_not_blocked_by_unavailable_tool_policy(monkeypatch):
    fake = types.ModuleType("src.spec_router")
    fake.LAST_DECISIONS = [{"choice": "answer_directly", "top": []}]
    fake.latest_decision = lambda: fake.LAST_DECISIONS[-1]
    fake.spec_route_tools = lambda *a, **kw: (set(), True)
    monkeypatch.setitem(sys.modules, "src.spec_router", fake)
    monkeypatch.setattr(agent_loop, "_configured_model_tool_surface_raw", lambda *a, **kw: "spec")
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setenv("ODYSSEUS_SPEC_MODE", "strict")
    contract = resolve_turn_contract(
        capabilities=set(), required_capabilities=set(),
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(disabled_tools=frozenset({"private_browser"})),
        message="hello",
    )
    routed = agent_loop._spec_router_contract(
        contract, [{"role": "user", "content": "hello"}],
        {"private_browser"}, "http://fixture", "Ajax", "fixture", {},
    )
    assert "private_browser" not in routed.unavailable


@pytest.mark.asyncio
async def test_contractless_spec_run_uses_the_compact_path(monkeypatch):
    # Scheduled, skill and monitor runs call the loop without a turn contract.
    # Without a contract no Spec routing applies, so the run must proceed like
    # the compact surface instead of reporting a refusal as its output.
    monkeypatch.setattr(agent_loop, "_configured_model_tool_surface_raw", lambda *a, **kw: "spec")
    reached = []

    async def provider(*args, **kwargs):
        reached.append(True)
        raise RuntimeError("provider reached")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", provider)
    events = [chunk async for chunk in agent_loop.stream_agent_loop(
        endpoint_url="http://fixture", model="Ajax",
        messages=[{"role": "user", "content": "hello"}],
        turn_contract=None, disabled_tools=set(), tool_policy=ToolPolicy(),
        _is_teacher_run=True,
    )]
    assert reached
    assert not any("requires a turn contract" in chunk for chunk in events)


@pytest.mark.asyncio
async def test_contractless_spec_run_gains_no_tool_authority(monkeypatch):
    # A contract-less Spec run must offer exactly what the compact surface
    # offers under the same policy, and must never consult the router.
    import src.spec_router as spec_router

    def router_consulted(*args, **kwargs):
        raise AssertionError("contract-less Spec run consulted the router")

    monkeypatch.setattr(spec_router, "spec_route_tools", router_consulted)
    policy = ToolPolicy(disabled_tools=frozenset({"manage_calendar"}))

    async def offered_tools(surface):
        monkeypatch.setattr(agent_loop, "_configured_model_tool_surface_raw", lambda *a, **kw: surface)
        menus = []

        async def provider(*args, **kwargs):
            menus.append(sorted((tool.get("function") or {}).get("name") or ""
                                for tool in kwargs.get("tools") or ()))
            raise RuntimeError("stop after the first provider request")
            yield  # pragma: no cover

        monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", provider)
        with pytest.raises(RuntimeError, match="stop after"):
            async for _ in agent_loop.stream_agent_loop(
                endpoint_url="http://fixture", model="Ajax",
                messages=[{"role": "user", "content": "list my notes and add a calendar event tomorrow at 9"}],
                turn_contract=None, disabled_tools={"manage_calendar"}, tool_policy=policy,
                _is_teacher_run=True,
            ):
                pass
        return menus

    spec_menus = await offered_tools("spec")
    assert spec_menus and spec_menus == await offered_tools("compact")
    assert all("manage_calendar" not in menu for menu in spec_menus)
