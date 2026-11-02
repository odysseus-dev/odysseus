"""Research is offered in compact chat, not automatically executed by keywords."""
import json
import pytest

from src.clean_agent_preview import PREVIEW_TOOLS, compact_schemas, evaluate_preview_call
from src.tool_policy import ToolPolicy
from src.tool_routing_experiment import MODEL_CHOICE_MODE, select_experiment_inventory
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract, resolve_turn_contract


def selected(prompt, disabled=(), mode=MODEL_CHOICE_MODE, history=()):
    policy = ToolPolicy(disabled_tools=frozenset(disabled))
    inventory = resolve_full_inventory_contract(
        schemas=[s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in PREVIEW_TOOLS], policy=policy)
    routed = resolve_turn_contract(capabilities=set(), schemas=FUNCTION_TOOL_SCHEMAS, policy=policy)
    return select_experiment_inventory(inventory, routed, history, mode, user_text=prompt)


@pytest.mark.parametrize('prompt', [
    'research ai info', 'can you research this', 'researhc ai info', 'reserach AI',
    'reserch ai info', 'can u reserch this', 'RESERACH AI info', 'researchh AI info',
    'do some resarch on batteries', 'research', 'what is research?', 'dont research this',
])
def test_research_hint_offers_both_tools_without_forcing_execution(prompt):
    contract = selected(prompt)
    assert contract.permits('trigger_research')
    assert contract.permits('manage_research')
    assert not contract.required and contract.required_read_operation is None
    assert any(s['function']['name'] == 'trigger_research' for s in compact_schemas(contract.schemas()))


def test_offered_research_job_executes_without_opening_other_network_side_effects():
    contract = selected('research ai info')
    assert evaluate_preview_call('trigger_research', {'topic': 'AI info'}, 'research ai info',
        turn_authorized_families=contract.active_capabilities).allowed
    assert not evaluate_preview_call('trigger_research', {'topic': 'AI info'}, 'Hello').allowed
    for tool, args in [('generate_image', {'prompt': 'AI'}),
                       ('send_email', {'to': 'nobody@example.com', 'body': 'AI'})]:
        assert not evaluate_preview_call(tool, args, 'research ai info',
            turn_authorized_families=contract.active_capabilities).allowed


@pytest.mark.parametrize('prompt', ['search AI info', 'Hi', 'What is a neural network?'])
def test_unrelated_prompt_does_not_gain_research(prompt):
    assert not selected(prompt).permits('trigger_research')


def test_disabled_research_cannot_be_restored_by_hint_or_history():
    history = [{'role': 'assistant', 'metadata': {'tool_events': [
        {'tool': 'trigger_research', 'exit_code': 0},
    ]}}]
    contract = selected('researhc more', disabled=('trigger_research', 'manage_research'), history=history)
    assert not contract.permits('trigger_research') and not contract.permits('manage_research')
    assert 'trigger_research' not in contract.executable
    assert not selected('research AI', mode='baseline').permits('trigger_research')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 503])
async def test_chat_dispatches_research_and_streams_only_confirmed_job_link(monkeypatch, status):
    import src.clean_agent_preview as preview
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    posted = []
    responses = iter([
        {'tool_calls': [{'index': 0, 'id': 'research-call', 'function': {
            'name': 'trigger_research', 'arguments': '{"topic":"AI info","max_rounds":1}',
        }}]},
        {'content': 'Research is running.' if status == 200 else 'Research service is unavailable.'},
    ])

    class Response:
        status_code = status
        text = 'service unavailable'
        def json(self): return {'session_id': 'fixture-research-job'}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': next(responses)}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
        async def post(self, url, **kwargs):
            posted.append((url, kwargs['json']))
            return Response()

    # Only HTTP boundaries are replaced: model transport + research service.
    # Tool conversion, policy, dispatcher and research implementation are real.
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    raw = [chunk async for chunk in preview.stream_preview(
        endpoint_url='http://model.test', model='test', headers={},
        messages=[{'role': 'user', 'content': 'research ai info'}],
        turn_contract=selected('research ai info'), session_id='fixture-chat', owner='fixture-owner',
        disabled_tools=set(), tool_policy=ToolPolicy(),
    )]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert len(posted) == 1 and posted[0][0].endswith('/api/research/start')
    assert posted[0][1] == {'query': 'AI info', 'max_rounds': 1,
        'origin_chat_id': 'fixture-chat', 'max_time': 120}
    tool = next(e for e in events if e.get('type') == 'tool_output')
    assert tool['execution_attempted'] and not tool['blocked']
    assert tool['error'] == (status != 200)
    streamed = ''.join(e.get('delta', '') for e in events)
    notices = [e for e in events if e.get('type') == 'ui_control']
    if status == 200:
        assert notices[0]['data']['research_session_id'] == 'fixture-research-job'
        assert streamed.count('](#research-fixture-research-job)') == 1
    else:
        assert not notices and '#research-' not in streamed
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['clean_v3_turn'][-1]['content'] == streamed
