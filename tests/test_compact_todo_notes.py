import json

import pytest

from src.clean_agent_preview import compact_schemas, stream_preview
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract


SCHEMA = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')


def test_compact_notes_preserves_checklist_creation_guidance():
    function = compact_schemas([SCHEMA], model='Ajax')[0]['function']
    assert 'note_type="checklist"' in function['description']
    assert 'auto-dated' in function['description']
    assert 'one {text, done:false} per task' in function['parameters']['properties']['checklist_items']['description']


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_note_link_is_preserved_only_after_success(monkeypatch, failed):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call1', 'type': 'function',
            'function': {'name': 'manage_notes', 'arguments': json.dumps({
                'action': 'add', 'title': 'To-do - 2026-09-29', 'note_type': 'checklist',
                'checklist_items': [{'text': 'Drop keys', 'done': False}, {'text': 'Meeting 2pm', 'done': False}],
            })}}]}}]},
        {'choices': [{'delta': {'content': 'Could not save.' if failed else 'Saved your checklist.'}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    async def execute(block, **kwargs):
        return block.tool_type, {'response': 'Failed' if failed else 'Created',
                                 'note_id': 'test-note', 'exit_code': int(failed)}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    contract = resolve_full_inventory_contract(schemas=[SCHEMA], policy=ToolPolicy())
    from dataclasses import replace
    contract = replace(contract, capabilities=frozenset({'notes'}))
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='Ajax', messages=[{'role': 'user', 'content': 'Make todo, drop keys, meeting 2pm'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=2,
    )]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    visible = ''.join(e.get('delta', '') for e in events)
    assert ('[Open note](/#open=notes&note=test-note)' in visible) is not failed
    if not failed:
        assert len(requests) == 1
