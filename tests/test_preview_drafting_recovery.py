import json
from types import SimpleNamespace

import jsonschema
import pytest

from src.clean_agent_preview import compact_schemas, conversation, stream_preview
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract


def test_explicit_thinking_off_wins_without_tools(monkeypatch):
    import src.clean_agent_preview as module
    monkeypatch.setattr(module, 'uses_odysseus_progressive_thinking', lambda model: True)
    assert module.progressive_thinking_for_turn('Ajax', []) is True
    assert module.progressive_thinking_for_turn('Ajax', [], 'off') is False


def test_fetch_requires_a_page_in_full_and_compact_contracts():
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'web_fetch')
    # The preview validates calls against the compact contract, which requires
    # url or urls. The full schema is sent to providers whose top-level
    # parameter contract rejects anyOf (test_model_tool_modes), so it accepts
    # both forms without the combinator.
    compact = compact_schemas([schema])[0]['function']['parameters']
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'query': 'compose a letter'}, compact)
    for params in [schema['function']['parameters'], compact]:
        jsonschema.validate({'url': 'https://example.com', 'query': 'details'}, params)
        jsonschema.validate({'urls': ['https://example.com']}, params)


@pytest.mark.parametrize('reply', ['you suck', 'thanks', 'a complaint', 'friendly'])
def test_clarification_history_keeps_task_recipient_and_question(reply):
    history = SimpleNamespace(history=[
        {'role': 'user', 'content': 'write email'},
        {'role': 'assistant', 'content': 'Who is it for?'},
        {'role': 'user', 'content': 'Jordan'},
        {'role': 'assistant', 'content': 'What do you want to say to Jordan?'},
    ])
    result = conversation(history, [{'role': 'user', 'content': reply}])
    assert [m['content'] for m in result] == [
        'write email', 'Who is it for?', 'Jordan',
        'What do you want to say to Jordan?', reply,
    ]


@pytest.mark.asyncio
async def test_bad_fetch_gets_correction_not_permission_refusal(monkeypatch):
    import src.clean_agent_preview as module
    requests = []
    responses = iter([
        {'tool_calls': [{'index': 0, 'id': 'bad-fetch', 'type': 'function',
                         'function': {'name': 'web_fetch', 'arguments': '{"query":"compose a letter"}'}}]},
        {'content': 'Dear Jordan,\nGreetings from under the sea!'},
    ])

    class Response:
        def __init__(self, delta): self.delta = delta
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': self.delta}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(json.loads(json.dumps(kwargs['json'])))
            return Response(next(responses))

    async def execute(*args, **kwargs):
        pytest.fail('Malformed fetch must never execute')

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'web_fetch']
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    events = [json.loads(chunk[6:]) async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'write an email as a fish to Jordan'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=policy,
    ) if '[DONE]' not in chunk]
    assert len(requests) == 2
    correction = requests[1]['messages']
    assert any('requires url or urls' in str(m.get('content')) for m in correction)
    assert any('not a permission denial' in str(m.get('content')) for m in correction)
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['clean_v3_turn'][-1]['content'].startswith('Dear Jordan')
    assert metrics['tool_schema_names'] == ['web_fetch']
    assert metrics['tool_schema_count'] == len(metrics['tool_schema_names'])


@pytest.mark.asyncio
async def test_ajax_complete_draft_has_no_lookup_or_delivery_tools(monkeypatch):
    import src.clean_agent_preview as module
    import src.email_task_intent as intent_module
    requests = []
    classifications = []
    async def classify(*args, **kwargs):
        classifications.append(kwargs)
        kwargs['accounting'].update(input_tokens=100, output_tokens=20, usage_source='real')
        return intent_module.EmailTaskIntent('draft', (), 'Thank Jon for lunch')
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'Hi Jon, thanks for lunch!'}}]})
            yield 'data: ' + json.dumps({'choices': [], 'usage': {'prompt_tokens': 200, 'completion_tokens': 30}})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response()
    async def execute(*args, **kwargs):
        pytest.fail('A fully supplied chat draft needs no tool execution')
    monkeypatch.setattr(intent_module, 'classify_email_task', classify)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'web_fetch', 'ask_user', 'read_email'}]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    events = [json.loads(chunk[6:]) async for chunk in stream_preview(
        endpoint_url='http://test', model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Draft an email thanking Jon for lunch'}],
        session_id='test', owner='test', disabled_tools=set(), tool_policy=policy,
        thinking_mode='off',
    ) if '[DONE]' not in chunk]
    assert len(requests) == 1
    assert len(classifications) == 1
    assert not requests[0].get('tools')
    assert any(e.get('stage') == 'email_task_scope' for e in events)
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['input_tokens'] == 300
    assert metrics['output_tokens'] == 50
    assert metrics['injected_tokens'] == metrics['last_request_tokens'] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'invalid_json'])
async def test_ajax_scope_failure_finishes_protocol_without_execution(monkeypatch, failure):
    import httpx
    import src.clean_agent_preview as module
    import src.email_task_intent as intent_module

    async def classify(*args, **kwargs):
        if failure == 'timeout':
            raise httpx.ReadTimeout('fixture')
        kwargs['accounting'].update(input_tokens=101, output_tokens=12, usage_source='real')
        raise ValueError('Malformed classifier output')

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            pytest.fail('Unknown scope must not generate with broad tools')

    async def execute(*args, **kwargs):
        pytest.fail('Unknown scope must not execute')

    monkeypatch.setattr(intent_module, 'classify_email_task', classify)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'web_fetch', 'read_email'}]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    chunks = [c async for c in stream_preview(
        endpoint_url='http://test', model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Draft an email thanking Jon for lunch'}],
        session_id='test', owner='test', disabled_tools=set(), tool_policy=policy)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['email_task_scope']['failed'] is True
    assert metrics['agent_rounds'] == metrics['tool_calls'] == 0
    assert metrics['total_tokens'] == (113 if failure == 'invalid_json' else 0)
    assert 'No action was taken' in metrics['clean_v3_turn'][-1]['content']
    assert any(e.get('type') == 'final_response' for e in events)
    assert not any(e.get('delta') for e in events)
    assert chunks[-1] == 'data: [DONE]\n\n'
