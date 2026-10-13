import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "jobs.db"}')
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    with factory() as db:
        for sid, owner in [('origin', 'alice'), ('other', 'alice'), ('foreign', 'bob')]:
            db.add(database.Session(id=sid, owner=owner, name=sid, model='test', endpoint_url='http://model.test'))
        db.commit()
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_background_result_returns_once_to_origin_after_foreground_finishes(store):
    from src.background_tool_jobs import BackgroundToolJobs
    busy = {'origin'}
    calls = []
    async def summarize(session, payload):
        calls.append(payload)
        return 'The trial measured a 17% improvement.'
    jobs = BackgroundToolJobs(is_busy=lambda sid: sid in busy, summarize=summarize)
    jobs.register('rp-fixture', 'origin', 'alice', 'research', 'trial findings', 2)
    jobs.complete('rp-fixture', 'Results: 17% improvement.', [{'url': 'https://example.org/trial'}])
    await jobs.tick()
    assert jobs.list_for_chat('origin', 'alice')[0]['status'] == 'ready'
    assert not calls
    busy.clear()
    await jobs.tick()
    await jobs.tick()
    result = jobs.list_for_chat('origin', 'alice')
    assert len(calls) == 1
    assert result[0]['status'] == 'delivered'
    assert '17%' in result[0]['message']['content']
    assert '#research-rp-fixture' in result[0]['message']['content']
    assert jobs.list_for_chat('other', 'alice') == []
    assert jobs.list_for_chat('origin', 'bob') == []
    # Recreated worker models a server restart / repeated completion notice.
    again = BackgroundToolJobs(is_busy=lambda sid: False, summarize=summarize)
    again.complete('rp-fixture', 'duplicate notice', [])
    await again.tick()
    assert again.list_for_chat('origin', 'alice') == result


def test_chat_cards_expose_small_progress_and_honest_outcomes(store):
    from src.background_tool_jobs import BackgroundToolJobs
    class ResearchService:
        def get_status(self, job_id):
            return {'progress': {'phase': 'reading', 'round': 1, 'total_sources': 3,
                'private_internal_data': 'must not be polled'}}
    jobs = BackgroundToolJobs(is_busy=lambda sid: False, research_handler=ResearchService())
    jobs.register('rp-card', 'origin', 'alice', 'research', 'topic', 2)
    result = jobs.list_for_chat('origin', 'alice')[0]
    assert result['progress'] == {'phase': 'reading', 'round': 1, 'total_sources': 3}
    jobs.complete('rp-card', 'No information could be gathered.', [])
    result = jobs.list_for_chat('origin', 'alice')[0]
    assert result['outcome'] == 'no_sources'
    assert result['source_count'] == 0
    assert 'payload' not in result
    assert jobs.list_for_chat('origin', 'bob') == []


@pytest.mark.asyncio
async def test_foreground_can_start_during_summary_and_delivery_waits(store):
    from src.background_tool_jobs import BackgroundToolJobs, background_result_context
    from core.models import Session, ChatMessage
    from src.clean_agent_preview import conversation
    busy = set()
    async def summarize(session, payload):
        busy.add('origin')
        return 'Quick findings.'
    jobs = BackgroundToolJobs(is_busy=lambda sid: sid in busy, summarize=summarize)
    jobs.register('rp-second', 'origin', 'alice', 'research', 'evidence', 2)
    jobs.complete('rp-second', 'The hidden measurement is 314.', [{'url': 'https://example.org/evidence'}])
    await jobs.tick()
    assert jobs.list_for_chat('origin', 'alice')[0]['status'] == 'ready'
    busy.clear()
    await jobs.tick()
    assert jobs.list_for_chat('origin', 'alice')[0]['status'] == 'delivered'
    with store() as db:
        saved = db.query(database.ChatMessage).filter_by(session_id='origin').one()
        metadata = json.loads(saved.meta_data)
    session = Session(id='origin', name='test', endpoint_url='test', model='test', history=[
        ChatMessage('user', 'research evidence'), ChatMessage('assistant', 'Quick findings.', metadata),
    ])
    assert '314' in str(session.get_context_messages())
    assert '314' in str(conversation(session, [{'role': 'user', 'content': 'What was measured?'}]))
    assert '314' in str(background_result_context(metadata))


@pytest.mark.asyncio
async def test_foreign_or_deleted_chat_never_receives_result(store):
    from src.background_tool_jobs import BackgroundToolJobs
    async def summarize(*args): raise AssertionError('must not run')
    jobs = BackgroundToolJobs(is_busy=lambda sid: False, summarize=summarize)
    with pytest.raises(ValueError):
        jobs.register('rp-foreign', 'foreign', 'alice', 'research', 'query', 2)
    jobs.register('rp-deleted', 'origin', 'alice', 'research', 'query', 2)
    jobs.complete('rp-deleted', 'body', [])
    with store() as db:
        db.delete(db.get(database.Session, 'origin'))
        db.commit()
    await jobs.tick()
    assert jobs.list_for_chat('origin', 'alice') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('origin,requested,expected', [('origin', None, 2), ('origin', 0, 20), (None, None, 20)])
async def test_research_start_binds_owner_and_uses_quick_chat_default_only(store, monkeypatch, origin, requested, expected):
    from fastapi import FastAPI
    import httpx
    from routes.research import research_routes
    from src.background_tool_jobs import BackgroundToolJobs
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    monkeypatch.setattr(research_routes, 'resolve_endpoint', lambda *a, **kw: ('http://model.test', 'test', {}))
    started = []
    class ResearchService:
        def start_research(self, **kwargs): started.append(kwargs)
    app = FastAPI()
    app.state.background_tool_jobs = BackgroundToolJobs(is_busy=lambda sid: False)
    @app.middleware('http')
    async def identify(request, call_next):
        request.state.current_user = 'alice'
        return await call_next(request)
    app.include_router(research_routes.setup_research_routes(ResearchService()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        body = {'query': 'fixture'}
        if origin: body['origin_chat_id'] = origin
        if requested is not None: body['max_rounds'] = requested
        response = await client.post('/api/research/start', json=body)
        assert response.status_code == 200, response.text
        assert started[0]['max_rounds'] == expected
        assert bool(started[0]['on_complete']) == bool(origin)
        rows = (await client.get('/api/research/chat-jobs/origin')).json()['jobs']
        assert len(rows) == (1 if origin else 0)
        blocked = await client.post('/api/research/start', json={'query': 'fixture', 'origin_chat_id': 'foreign'})
        assert blocked.status_code == 404
        assert len(started) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('rounds', [None, 0, 5])
async def test_tool_cannot_spoof_origin_and_defaults_to_two_rounds(monkeypatch, rounds):
    import httpx
    from src.tools.research import do_trigger_research
    posted = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            posted.append(kwargs['json'])
            return httpx.Response(200, json={'session_id': 'rp-fixture'})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    arguments = {'topic': 'AI', 'origin_chat_id': 'foreign'}
    if rounds is not None: arguments['max_rounds'] = rounds
    result = await do_trigger_research(json.dumps(arguments),
        owner='alice', chat_session_id='origin')
    assert result['exit_code'] == 0
    expected = {'query': 'AI', 'origin_chat_id': 'origin', 'max_rounds': 2 if rounds is None else rounds}
    if rounds is None: expected['max_time'] = 120
    assert posted[0] == expected


@pytest.mark.asyncio
async def test_summary_makes_an_actual_model_request(monkeypatch):
    import httpx
    from types import SimpleNamespace
    from src import llm_core
    from src.background_tool_jobs import BackgroundToolJobs
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Aster measured 17% improvement.'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(llm_core, '_get_http_client', lambda: client)
        session = SimpleNamespace(id='summary-fixture', endpoint_url='http://summary-model.test/v1/chat/completions', model='fixture')
        result = await BackgroundToolJobs(is_busy=lambda sid: False)._summarize(session, {
            'job_id': 'rp-summary', 'query': 'Aster trial', 'report': 'Aster improved 17%.', 'sources': [], 'rounds': 2,
        })
    assert len(requests) == 1
    assert '17%' in result
    assert 'Aster improved 17%' in str(requests[0]['messages'])
    assert not requests[0].get('tools')


@pytest.mark.asyncio
async def test_failed_job_returns_honest_notice_without_model_summary(store):
    from src.background_tool_jobs import BackgroundToolJobs
    async def summarize(*args): raise AssertionError('No report to summarize')
    jobs = BackgroundToolJobs(is_busy=lambda sid: False, summarize=summarize)
    jobs.register('rp-error', 'origin', 'alice', 'research', 'query', 2)
    jobs.complete('rp-error', 'Research timed out before collecting evidence.', [], error=True)
    await jobs.tick()
    row = jobs.list_for_chat('origin', 'alice')[0]
    assert row['status'] == 'delivered'
    assert row['message']['content'] == 'Research timed out before collecting evidence.'
    assert '#research-' not in row['message']['content']


@pytest.mark.asyncio
async def test_unavailable_summary_model_cannot_stall_delivery_forever(store):
    import asyncio
    from src.background_tool_jobs import BackgroundToolJobs
    async def hanging_model(*args): await asyncio.Future()
    jobs = BackgroundToolJobs(is_busy=lambda sid: False, summarize=hanging_model, summary_timeout=0.01)
    jobs.register('rp-timeout', 'origin', 'alice', 'research', 'query', 2)
    jobs.complete('rp-timeout', 'The preserved source report.', [])
    await jobs.tick()
    row = jobs.list_for_chat('origin', 'alice')[0]
    assert row['status'] == 'delivered'
    assert 'could not generate' in row['message']['content']
    assert '#research-rp-timeout' in row['message']['content']
