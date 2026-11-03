"""Spec must never widen a request or discard its evidence obligations."""
import sys
import types
from dataclasses import replace

import pytest

from src import agent_loop
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import active_turn_contract, bind_turn_contract, resolve_turn_contract


def _contract():
    return resolve_turn_contract(
        capabilities={'notes'}, required_capabilities={'notes'},
        schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy(),
        selected_tools={'manage_notes', 'ask_user'}, message='List my notes',
    )


def _router(monkeypatch, picks, choice='t1', mode='strict'):
    fake = types.ModuleType('src.spec_router')
    fake.LAST_DECISIONS = [{'choice': choice, 'top': [(next(iter(picks), ''), .99)]}]
    fake.latest_decision = lambda: fake.LAST_DECISIONS[-1]
    fake.spec_route_tools = lambda *a, **kw: (picks, True)
    monkeypatch.setitem(sys.modules, 'src.spec_router', fake)
    monkeypatch.setattr(agent_loop, '_configured_model_tool_surface_raw', lambda *a, **kw: 'spec')
    monkeypatch.setattr(agent_loop, 'get_mcp_manager', lambda: None)
    monkeypatch.setenv('ODYSSEUS_SPEC_MODE', mode)


def _route(contract):
    return agent_loop._spec_router_contract(contract, [{'role': 'user', 'content': 'List my notes'}],
                                           set(), 'http://fixture', 'fixture', 'alice', {}, ToolPolicy())


@pytest.mark.parametrize('picks,choice', [({'bash'}, 't1'), (set(), 'answer_directly'), ({'ask_user'}, 't1')])
def test_router_cannot_replace_required_read_or_offer_other_tools(monkeypatch, picks, choice):
    _router(monkeypatch, picks, choice)
    contract = _contract()
    assert contract.required_read_operation is not None
    with bind_turn_contract(contract):
        routed = _route(contract)
        assert routed.required == contract.required
        assert routed.required_read_operation == contract.required_read_operation
        assert routed.offered <= contract.offered
        assert routed.executable == contract.executable
        assert active_turn_contract() is routed
    assert active_turn_contract() is None


def test_router_keeps_unavailable_read_outside_required_tool_set(monkeypatch):
    # The only representable read outside `required` is an explicitly
    # unavailable one; the router must not turn that refusal into a menu.
    _router(monkeypatch, {'ask_user'})
    base = _contract()
    contract = replace(base, required=frozenset(),
                       unavailable=frozenset({base.required_read_operation.tool}))
    with bind_turn_contract(contract):
        assert _route(contract) is contract
        assert active_turn_contract() is contract


def test_router_narrowing_keeps_read_and_executable_set(monkeypatch):
    _router(monkeypatch, {'manage_notes'})
    contract = _contract()
    with bind_turn_contract(contract):
        routed = _route(contract)
        assert routed is not contract and routed.routing_experiment == 'spec-router'
        assert routed.required == contract.required
        assert routed.required_read_operation == contract.required_read_operation
        assert routed.offered <= contract.offered
        assert routed.executable == contract.executable


@pytest.mark.parametrize('mode', ['off', 'unsupported'])
def test_off_and_unknown_modes_keep_contract_without_router(monkeypatch, mode):
    _router(monkeypatch, {'bash'}, mode=mode)
    contract = _contract()
    assert _route(contract) is contract


def test_router_exception_keeps_bound_contract(monkeypatch):
    _router(monkeypatch, {'bash'})
    sys.modules['src.spec_router'].spec_route_tools = lambda *a, **kw: 1 / 0
    contract = _contract()
    with bind_turn_contract(contract):
        assert _route(contract) is contract
        assert active_turn_contract() is contract


@pytest.mark.asyncio
async def test_driver_refuses_before_provider_or_dispatch(monkeypatch):
    from src import spec_driver
    monkeypatch.setattr(spec_driver, 'preview_model_response', lambda *a, **k: pytest.fail('provider called'))
    monkeypatch.setattr(spec_driver, 'execute_tool_block', lambda *a, **k: pytest.fail('tool called'))
    contract = _contract()
    with bind_turn_contract(contract):
        chunks = [chunk async for chunk in spec_driver.stream_spec_turn(
            endpoint_url='http://fixture', model='fixture', messages=[], headers={},
            session_id='s', owner='alice', disabled_tools=set(), tool_policy=ToolPolicy())]
        assert active_turn_contract() is contract
    assert 'unavailable' in ''.join(chunks)
    assert chunks[-1] == 'data: [DONE]\n\n'


@pytest.mark.asyncio
async def test_driver_close_restores_context_after_early_disconnect(monkeypatch):
    from src import spec_driver
    contract = _contract()
    with bind_turn_contract(contract):
        stream = spec_driver.stream_spec_turn(
            endpoint_url='http://fixture', model='fixture', messages=[], headers={},
            session_id='s', owner='alice', disabled_tools=set(), tool_policy=ToolPolicy())
        await anext(stream)
        await stream.aclose()
        assert active_turn_contract() is contract
    assert active_turn_contract() is None
