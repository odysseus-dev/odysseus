import json
from dataclasses import replace

import pytest

from src.clean_agent_preview import compact_schemas, stream_preview
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract

SCHEMA = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_calendar')


def test_compact_calendar_keeps_creation_field_meanings():
    f = compact_schemas([SCHEMA], model='Ajax')[0]['function']
    assert 'summary and local_start={date,time} in the SAME call' in f['description']
    props = f['parameters']['properties']
    assert 'dtstart' not in props and 'dtend' not in props
    assert props['local_start']['type'] == 'object'
    assert set(props['local_start']['properties']) == {'date', 'time'}
    assert 'Omit when creating' in f['parameters']['properties']['uid']['description']


@pytest.mark.asyncio
async def test_calendar_confirmation_uses_saved_result_not_invented_weekday(monkeypatch):
    import src.clean_agent_preview as module
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'type': 'function',
            'function': {'name': 'manage_calendar', 'arguments': json.dumps({
                'action': 'create_event', 'summary': 'Meeting', 'dtstart': '2026-09-30T14:00:00',
            })}}]}}]},
        {'choices': [{'delta': {'content': 'Created for Tuesday, September 30.'}}]},
    ])
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(next(responses))
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    confirmation = 'Created event [Meeting](#event-test-event) on 2026-09-30T14:00:00'
    async def execute(block, **kwargs):
        return block.tool_type, {'response': confirmation, 'uid': 'test-event',
            'dtstart': '2026-09-30T14:00:00', 'anchor': '[Meeting](#event-test-event)', 'exit_code': 0}
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    contract = replace(resolve_full_inventory_contract(schemas=[SCHEMA], policy=ToolPolicy()),
                       capabilities=frozenset({'calendar'}))
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='Ajax', messages=[{'role':'user','content':'Add calendar meeting today 2pm'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=2,
    )]
    events = [json.loads(c[6:]) for c in raw if '[DONE]' not in c]
    final = next(e['content'] for e in events if e.get('type') == 'final_response')
    assert confirmation in final
    assert 'Tuesday' not in final
