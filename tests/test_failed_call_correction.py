"""Real note storage and dispatcher, with only the model HTTP boundary scripted."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from src.clean_agent_preview import stream_preview
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract
from src.tools.notes import do_manage_notes


@pytest.mark.asyncio
@pytest.mark.parametrize('repeats', [2, 3])
async def test_corrected_ids_execute_after_repeated_ambiguous_title_failures(tmp_path, monkeypatch, repeats):
    import src.clean_agent_preview as preview
    engine = create_engine(f'sqlite:///{tmp_path / "notes.db"}')
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    with factory() as db:
        for id, title in [('target-a', 'same-title'), ('target-b', 'same-title'), ('keep-c', 'keep-me')]:
            db.add(database.Note(id=id, title=title, owner='fixture-owner', content='fixture'))
        db.commit()
    async def read(id):
        return await do_manage_notes(json.dumps({'action': 'view', 'id': id}), owner='fixture-owner')
    before = await read('keep-c')
    def calls(*args):
        return {'tool_calls': [{'index': i, 'id': f'call-{counter[0]}-{i}', 'function': {
            'name': 'manage_notes', 'arguments': json.dumps(a)}} for i, a in enumerate(args)]}
    counter = [0]
    responses = iter([calls({'action': 'delete', 'title': 'same-title'}) for _ in range(repeats)] + [
        calls({'action': 'delete', 'id': 'target-a'}, {'action': 'delete', 'id': 'target-b'}),
        {'content': 'Deleted both notes.'},
    ])
    requests = []
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            counter[0] += 1
            delta = next(responses)
            for call in delta.get('tool_calls', []):
                call['id'] = f'call-{counter[0]}-{call["index"]}'
            yield 'data: ' + json.dumps({'choices': [{'delta': delta}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response()
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=[schema], policy=policy)
    try:
        raw = [chunk async for chunk in stream_preview(endpoint_url='http://model.test', model='test',
            messages=[{'role': 'user', 'content': 'Delete both notes with the title same-title; keep keep-me.'}],
            headers={}, turn_contract=contract, session_id='fixture-delete', owner='fixture-owner',
            disabled_tools=set(), tool_policy=policy, max_rounds=8)]
        events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
        assert (await read('target-a'))['exit_code'] == 0
        assert (await read('target-b'))['exit_code'] == 0
        assert await read('keep-c') == before
        outputs = [e for e in events if e.get('type') == 'tool_output']
        attempts = [e for e in outputs if e.get('execution_attempted')]
        assert len(attempts) == 1
        assert attempts[0]['blocked'] is True
        assert 'missing or ambiguous' in attempts[0]['output']
        assert any('No changes were made' in e.get('content', '') for e in events if e.get('type') == 'final_response')
    finally:
        engine.dispose()
