import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src import clean_agent_preview as runner
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import (
    requested_capabilities,
    resolve_turn_contract,
    selected_tools_for_request,
)

PROMPT = 'Search the web for the Ajax population.'


def _contract(prompt=PROMPT):
    policy = ToolPolicy()
    selected = selected_tools_for_request(prompt)
    return resolve_turn_contract(
        capabilities=requested_capabilities(prompt),
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=policy,
        selected_tools=selected,
        message=prompt,
    ), policy


def _install_driver_router(monkeypatch, decision, picks=None):
    """Fake the router's request-local decision."""
    fake = types.ModuleType('src.spec_router')
    fake.think_router_enabled = lambda: False
    fake.LAST_DECISIONS = [decision]
    fake.latest_decision = lambda: fake.LAST_DECISIONS[-1]

    def _spec_route_tools(user_message, schemas, disabled_tools=None,
                          messages=None, return_decision=False):
        if picks is not None:
            chosen = picks
        elif decision.get('tool_name'):
            chosen = [decision['tool_name']]
        else:
            chosen = list(decision.get('tools') or [])
        return (chosen, True) if return_decision else chosen

    fake.spec_route_tools = _spec_route_tools
    monkeypatch.setitem(sys.modules, 'src.spec_router', fake)
    monkeypatch.setattr(
        'src.agent_loop._configured_model_tool_surface_raw',
        lambda *a, **k: 'spec', raising=False,
    )


def _fake_response(captured, scripts):
    pending = list(scripts)

    class Response:
        def __init__(self, script=None):
            self._s = script or {}

        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            calls = self._s.get('tool_calls')
            if calls:
                for i, c in enumerate(calls):
                    yield 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [
                        {'index': i, 'id': f'c{i}', 'type': 'function',
                         'function': {'name': c['name'],
                                      'arguments': c.get('arguments') or '{}'}}]}}]})
            else:
                content = self._s.get('content', 'Hi.')
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': content}}]})
            yield 'data: [DONE]'

    @asynccontextmanager
    async def response(*args, **kwargs):
        captured.append(args[3] if len(args) > 3 else kwargs.get('request'))
        yield Response(pending.pop(0) if pending else None)

    return response


async def _run(monkeypatch, captured, model='odysseus-agentic-v4', scripts=None,
               max_rounds=1):
    monkeypatch.setattr(
        runner, 'preview_model_response', _fake_response(captured, scripts or []))
    contract, policy = _contract()
    events = []
    async for chunk in runner.stream_preview(
        endpoint_url='http://fixture', model=model, headers={},
        messages=[{'role': 'user', 'content': PROMPT}], turn_contract=contract,
        session_id='spec-driver', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=max_rounds,
    ):
        if chunk.startswith('data: ') and '[DONE]' not in chunk:
            events.append(json.loads(chunk[6:]))
    return contract, events


def _install_recorder(monkeypatch):
    executed = []

    async def _rec(block, *args, **kwargs):
        executed.append(getattr(block, 'tool_type', None))
        return 'ok', {'exit_code': 0, 'output': 'ok'}

    monkeypatch.setattr(runner, 'execute_tool_block', _rec)
    return executed


def test_spec_driver_active_gate(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_MODE', 'driver')
    monkeypatch.setattr(
        'src.agent_loop._configured_model_tool_surface_raw',
        lambda *a, **k: 'spec', raising=False)
    assert runner.spec_driver_active('http://x', 'm', 'o', {}) is True
    monkeypatch.setenv('ODYSSEUS_SPEC_MODE', 'strict')
    assert runner.spec_driver_active('http://x', 'm', 'o', {}) is False
    monkeypatch.setenv('ODYSSEUS_SPEC_MODE', 'driver')
    monkeypatch.setattr(
        'src.agent_loop._configured_model_tool_surface_raw',
        lambda *a, **k: 'compact', raising=False)
    assert runner.spec_driver_active('http://x', 'm', 'o', {}) is False


def test_spec_driver_pick_shapes(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_MODE', 'driver')
    # tool pick at any confidence
    _install_driver_router(monkeypatch, {
        'message': PROMPT, 'choice': 't3', 'tools': ['web_fetch'],
        'top': [('web_fetch', 0.12)], 'answer_directly': 0.2, 'ask_user': 0.1,
    })
    pick = runner.spec_driver_pick([{'role': 'user', 'content': PROMPT}], set(), PROMPT)
    assert pick == {'choice': 'tool', 'tool_name': 'web_fetch', 'p': 0.12}

    # tool pick below MIN_CONF: the router return is empty but top-1 is a tool
    _install_driver_router(monkeypatch, {
        'message': PROMPT, 'choice': 't5', 'tools': [],
        'top': [('web_fetch', 0.12)], 'answer_directly': 0.2, 'ask_user': 0.1,
    }, picks=[])
    pick = runner.spec_driver_pick([], set(), PROMPT)
    assert pick == {'choice': 'tool', 'tool_name': 'web_fetch', 'p': 0.12}

    # answer_directly
    _install_driver_router(monkeypatch, {
        'message': PROMPT, 'choice': 'answer_directly', 'tools': [],
        'top': [('answer_directly', 0.4)], 'answer_directly': 0.4, 'ask_user': 0.1,
    })
    pick = runner.spec_driver_pick([], set(), PROMPT)
    assert pick['choice'] == 'answer_directly' and pick['p'] == 0.4

    # ask_user
    _install_driver_router(monkeypatch, {
        'message': PROMPT, 'choice': 'ask_user', 'tools': [],
        'top': [('ask_user', 0.3)], 'answer_directly': 0.1, 'ask_user': 0.3,
    })
    pick = runner.spec_driver_pick([], set(), PROMPT)
    assert pick['choice'] == 'ask_user'

    # router error / empty decision -> fall back to the normal menu
    _install_driver_router(monkeypatch, {'message': PROMPT, 'error': 'timeout'})
    assert runner.spec_driver_pick([], set(), PROMPT) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('decision,picks', [
    ({'choice': 't1', 'top': [('web_fetch', 0.9)]}, ['web_fetch']),
    ({'choice': 'answer_directly', 'top': []}, []),
    ({'choice': None, 'error': 'timeout'}, []),
    ({'choice': 't1', 'top': [('manage_calendar', 0.9)]}, ['manage_calendar']),
    ({'choice': 't1', 'top': [('totally_missing_tool', 0.9)]}, ['totally_missing_tool']),
])
async def test_spec_driver_refuses_before_router_model_or_tools(monkeypatch, decision, picks):
    monkeypatch.setenv('ODYSSEUS_SPEC_MODE', 'driver')
    _install_driver_router(monkeypatch, decision, picks=picks)
    router = sys.modules['src.spec_router']
    router.spec_route_tools = lambda *a, **k: pytest.fail('driver called router')
    executed = _install_recorder(monkeypatch)
    captured = []
    _, events = await _run(monkeypatch, captured)
    assert captured == []
    assert executed == []
    assert any('unavailable' in event.get('delta', '') for event in events)


@pytest.mark.asyncio
async def test_driver_configuration_is_not_rechecked_mid_turn(monkeypatch):
    calls = []
    def active(*args):
        calls.append(True)
        return len(calls) > 1
    monkeypatch.setattr(runner, 'spec_driver_active', active)
    captured = []
    await _run(monkeypatch, captured)
    assert captured
    assert len(calls) == 1


def test_spec_driver_wired_in_stream_preview():
    src = Path(runner.__file__).read_text()
    assert '[spec-driver] round=' in src
    assert 'spec_driver_pick(' in src
    assert '[spec-driver] dropped off-pick call name=' in src


def test_spec_contract_offered_extends_the_model_runtime(monkeypatch):
    """A Spec pick outside PREVIEW_TOOLS must still be runnable: on a
    spec-router turn the contract's offered set is part of the runtime."""
    import types
    from src import turn_contract as tc
    from src.clean_agent_preview import PREVIEW_TOOLS, evaluate_preview_call

    assert 'get_weather' not in PREVIEW_TOOLS

    class _C:
        routing_experiment = 'spec-router'
        offered = frozenset({'get_weather'})

    monkeypatch.setattr(tc, 'active_turn_contract', lambda: _C())
    allowed = evaluate_preview_call(
        'get_weather', {'location': 'Tokyo'}, 'weather',
        turn_authorized_families=frozenset(),
    )
    assert allowed.allowed, allowed

    class _Plain:
        routing_experiment = 'baseline'
        offered = frozenset({'get_weather'})

    monkeypatch.setattr(tc, 'active_turn_contract', lambda: _Plain())
    denied = evaluate_preview_call(
        'get_weather', {'location': 'Tokyo'}, 'weather',
        turn_authorized_families=frozenset(),
    )
    assert not denied.allowed
    assert denied.reason == 'tool_not_in_model_runtime'
