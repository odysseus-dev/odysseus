import json
import sys
import types
from contextlib import asynccontextmanager

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


def _install_spec_router(monkeypatch, *, p):
    fake = types.ModuleType('src.spec_router')
    fake.LAST_DECISIONS = [
        {'message': PROMPT, 'choice': 'answer_directly', 'answer_directly': p},
    ]
    fake.latest_decision = lambda: fake.LAST_DECISIONS[-1]
    monkeypatch.setitem(sys.modules, 'src.spec_router', fake)
    monkeypatch.setattr(
        'src.agent_loop._configured_model_tool_surface_raw',
        lambda *a, **k: 'spec', raising=False,
    )


def _fake_response(captured):
    class Response:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'Hi there.'}}]})
            yield 'data: [DONE]'

    @asynccontextmanager
    async def response(*args, **kwargs):
        request = args[3] if len(args) > 3 else kwargs.get('request')
        captured.append(request)
        yield Response()

    return response


async def _run(monkeypatch, captured):
    monkeypatch.setattr(runner, 'preview_model_response', _fake_response(captured))
    contract, policy = _contract()
    events = []
    async for chunk in runner.stream_preview(
        endpoint_url='http://fixture', model='Ajax', headers={},
        messages=[{'role': 'user', 'content': PROMPT}], turn_contract=contract,
        session_id='spec-direct', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=1,
    ):
        if chunk.startswith('data: ') and '[DONE]' not in chunk:
            events.append(json.loads(chunk[6:]))
    return contract, events


def _sent_tools(captured):
    first = captured[0]
    return {(s.get('function') or {}).get('name') for s in (first.get('tools') or [])}


async def _control_tools(monkeypatch):
    """Tools offered for the identical request with the spec feature disabled.

    Upstream's harness applies its own round-1 tool policy (bounded research,
    permitted-schema filtering), so the spec feature is only required to leave
    that list untouched rather than match the raw contract.offered set.
    """
    monkeypatch.delenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', raising=False)
    control = []
    await _run(monkeypatch, control)
    assert control
    return _sent_tools(control)


@pytest.mark.asyncio
async def test_spec_direct_round1_forces_tool_choice_none_with_same_tools(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    _install_spec_router(monkeypatch, p=0.971)
    captured = []
    contract, _events = await _run(monkeypatch, captured)

    assert captured, 'stream_preview made a provider round'
    first = captured[0]
    assert first.get('tool_choice') == 'none'
    sent = _sent_tools(captured)
    assert sent == await _control_tools(monkeypatch)  # tools list unchanged by spec
    assert sent  # the offered set is non-empty here


@pytest.mark.asyncio
async def test_spec_direct_low_confidence_is_unchanged(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    _install_spec_router(monkeypatch, p=0.5)
    captured = []
    await _run(monkeypatch, captured)
    assert captured
    assert captured[0].get('tool_choice') != 'none'


@pytest.mark.asyncio
async def test_spec_direct_env_off_is_identical(monkeypatch):
    monkeypatch.delenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', raising=False)
    _install_spec_router(monkeypatch, p=0.971)
    captured = []
    await _run(monkeypatch, captured)
    assert captured
    assert captured[0].get('tool_choice') != 'none'


@pytest.mark.asyncio
async def test_spec_direct_router_error_is_unchanged(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')

    class _Boom:
        def latest_decision(self):
            raise RuntimeError('router down')
    monkeypatch.setitem(sys.modules, 'src.spec_router', _Boom())
    monkeypatch.setattr(
        'src.agent_loop._configured_model_tool_surface_raw',
        lambda *a, **k: 'spec', raising=False,
    )
    captured = []
    await _run(monkeypatch, captured)
    assert captured
    assert captured[0].get('tool_choice') != 'none'


def _fake_response_with_text(captured, text):
    class Response:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: ' + json.dumps(
                {'choices': [{'delta': {'content': text}}]})
            yield 'data: [DONE]'

    @asynccontextmanager
    async def response(*args, **kwargs):
        request = args[3] if len(args) > 3 else kwargs.get('request')
        captured.append(request)
        yield Response()

    return response


def test_spec_direct_mode_helper_defaults_to_tool_choice_none(monkeypatch):
    monkeypatch.delenv('ODYSSEUS_SPEC_DIRECT_MODE', raising=False)
    assert runner.spec_direct_mode() == 'tool_choice_none'
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_MODE', 'empty_menu')
    assert runner.spec_direct_mode() == 'empty_menu'
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_MODE', 'nonsense')
    assert runner.spec_direct_mode() == 'tool_choice_none'


@pytest.mark.asyncio
async def test_spec_direct_empty_menu_sends_no_tools_and_thinking_off(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_MODE', 'empty_menu')
    _install_spec_router(monkeypatch, p=0.971)
    captured = []
    await _run(monkeypatch, captured)

    assert captured, 'stream_preview made a provider round'
    first = captured[0]
    assert not (first.get('tools') or [])  # no tools offered at all
    assert first.get('tool_choice') in (None, 'none')
    # Think router did not decide -> thinking stays off for the round.
    assert first.get('chat_template_kwargs', {}).get('enable_thinking') is False


@pytest.mark.asyncio
async def test_spec_direct_default_mode_keeps_tool_choice_none(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_MODE', 'tool_choice_none')
    _install_spec_router(monkeypatch, p=0.971)
    captured = []
    contract, _events = await _run(monkeypatch, captured)

    assert captured
    first = captured[0]
    assert first.get('tool_choice') == 'none'
    sent = _sent_tools(captured)
    assert sent == await _control_tools(monkeypatch)  # tools list unchanged by spec


@pytest.mark.asyncio
async def test_spec_direct_empty_menu_does_not_execute_text_tool_call(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_MODE', 'empty_menu')
    _install_spec_router(monkeypatch, p=0.971)

    executed = []

    async def _recorder(*args, **kwargs):
        executed.append(args)
        return ('', {'exit_code': 0})
    monkeypatch.setattr(runner, 'execute_tool_block', _recorder, raising=False)

    markup = ('Sure thing. '
              '<tool_call>{"name": "web_search", "arguments": {"query": "Ajax"}}'
              '</tool_call> Anything else?')
    monkeypatch.setattr(
        runner, 'preview_model_response', _fake_response_with_text([], markup))

    contract, policy = _contract()
    events = []
    async for chunk in runner.stream_preview(
        endpoint_url='http://fixture', model='Ajax', headers={},
        messages=[{'role': 'user', 'content': PROMPT}], turn_contract=contract,
        session_id='spec-direct-empty', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=1,
    ):
        if chunk.startswith('data: ') and '[DONE]' not in chunk:
            events.append(json.loads(chunk[6:]))

    assert executed == []  # nothing executed: nothing was offered
    visible = ''.join(
        str(e.get('content') or e.get('delta') or '') for e in events)
    assert '<tool_call' not in visible  # markup stripped from the answer
    assert 'web_search' not in visible
