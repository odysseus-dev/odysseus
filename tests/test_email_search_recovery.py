import json

import pytest

from src.clean_agent_preview import email_search_recovery, email_search_result_empty, stream_preview
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract


@pytest.mark.parametrize('value', [
    'No emails matched "drop off the keys address".',
    {'stdout': 'No emails matched "keys".', 'exit_code': 0},
    json.dumps({'output': json.dumps({'stdout': 'No emails matched "keys".', 'exit_code': 0})}),
])
def test_empty_results(value):
    assert email_search_result_empty(value)
    assert email_search_recovery(value, 0)
    assert email_search_recovery(value, 1)
    assert not email_search_recovery(value, 2)


@pytest.mark.parametrize('value', [
    {'stdout': 'No emails matched "keys".', 'exit_code': 1},
    {'stdout': 'No emails matched "keys".', 'stderr': 'Authentication failed'},
    {'error': 'Timeout'}, '', None,
    'UID 42: Key return address',
    'Email body: No emails matched "keys".',
])
def test_nonempty_and_failed_results_do_not_trigger(value):
    assert not email_search_result_empty(value)


@pytest.mark.asyncio
async def test_recovery_reaches_next_model_request(monkeypatch):
    import src.clean_agent_preview as module
    requests = []
    executions = []
    responses = iter([
        {'tool_calls': [{'index': 0, 'id': 'c1', 'type': 'function', 'function': {
            'name': 'mcp__email__search_emails', 'arguments': '{"query":"drop off the keys address"}'}}]},
        {'tool_calls': [{'index': 0, 'id': 'c2', 'type': 'function', 'function': {
            'name': 'mcp__email__search_emails', 'arguments': '{"query":"keys"}'}}]},
        {'content': 'Found a possible message about returning keys.'},
    ])
    class Response:
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
        def stream(self, *args, **kwargs):
            requests.append(json.loads(json.dumps(kwargs['json'])))
            return Response()
    async def execute(block, **kwargs):
        executions.append(block)
        return block.tool_type, {'exit_code': 0, 'output': json.dumps({
            'exit_code': 0, 'stdout': 'No emails matched "drop off the keys address".'
            if len(executions) == 1 else 'UID 42: Key return address'})}
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    import src.email_task_intent as intent_module
    async def classify(*args, **kwargs):
        return intent_module.EmailTaskIntent('read', ('email',), 'Find key return address')
    monkeypatch.setattr(intent_module, 'classify_email_task', classify)
    schema = {'type': 'function', 'function': {'name': 'mcp__email__search_emails',
        'description': 'Search email', 'parameters': {'type': 'object',
        'properties': {'query': {'type': 'string'}}, 'required': ['query']}}}
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=[schema], policy=policy)
    chunks = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Search my email, what address do I drop off the keys'}],
        session_id='test-email-recovery', owner='fixture', disabled_tools=set(),
        tool_policy=policy, max_rounds=3,
    )]
    assert len(executions) == 2, chunks
    guidance = [m for m in requests[1]['messages'] if 'shorter targeted query' in str(m.get('content'))]
    assert guidance
    assert 'Read promising messages' in guidance[0]['content']
    assert 'Preserve explicit account' in guidance[0]['content']
