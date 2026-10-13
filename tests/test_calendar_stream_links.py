"""Calendar confirmation links through real storage, dispatcher and SSE output."""
import json
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from src.clean_agent_preview import stream_preview
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['create', 'update', 'already_linked', 'failed_update'])
async def test_event_confirmation_stream_preserves_only_successful_links(tmp_path, monkeypatch, case):
    import src.clean_agent_preview as preview
    engine = create_engine(f'sqlite:///{tmp_path / "calendar.db"}')
    database.Base.metadata.create_all(engine)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    monkeypatch.setattr(database, 'SessionLocal', sessionmaker(bind=engine))
    from src.tools.calendar import do_manage_calendar
    args = {'action': 'create_event', 'summary': 'Fixture vet', 'dtstart': '2030-01-01T09:00:00'}
    answer = 'Done. Your vet appointment is scheduled.'
    if case != 'create':
        seed = await do_manage_calendar(json.dumps(args), owner='fixture-owner')
        assert seed['exit_code'] == 0
        args = {'action': 'update_event', 'uid': seed['uid'], 'summary': 'Fixture vet updated'}
        if case == 'already_linked':
            answer += f' [Vet](#event-{seed["uid"]})'
        if case == 'failed_update':
            args['uid'] = 'missing-event'
            answer = 'I could not find that event.'
    replies = iter([
        {'tool_calls': [{'index': 0, 'id': 'create-1', 'function': {
            'name': 'manage_calendar', 'arguments': json.dumps(args)}}]},
        {'content': answer},
    ])

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': next(replies)}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()

    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_calendar')
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=[schema], policy=policy)
    try:
        raw = [chunk async for chunk in stream_preview(
            endpoint_url='http://model.test', model='test', headers={},
            messages=[{'role': 'user', 'content': 'Add Fixture vet on January 1, 2030 at 9 AM.' if case == 'create'
                       else 'Update my calendar event title to Fixture vet updated.'}],
            turn_contract=contract, session_id='fixture-links', owner='fixture-owner',
            disabled_tools=set(), tool_policy=policy,
        )]
        events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
        tool = next(e for e in events if e.get('type') == 'tool_output')
        assert tool['error'] == (case == 'failed_update')
        saved = await do_manage_calendar(json.dumps({
            'action': 'list_events', 'start': '2030-01-01', 'end': '2030-01-02',
        }), owner='fixture-owner')
        target = re.search(r'#event-([\w-]+)', saved['response']).group(0)
        streamed = ''.join(e.get('delta', '') for e in events)
        if case == 'failed_update':
            assert '#event-' not in streamed
        else:
            assert streamed.count(f']({target})') == 1
        assert streamed.startswith(answer)
        assert not any(e.get('type') == 'final_response' for e in events)
        metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
        assert metrics['clean_v3_turn'][-1]['content'] == streamed
        assert raw[-1] == 'data: [DONE]\n\n'
    finally:
        engine.dispose()
