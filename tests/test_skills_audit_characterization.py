"""Offline contracts captured against the merged skills baseline before extraction."""
import asyncio
import hashlib
import inspect
import json
from contextvars import ContextVar
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from routes import skills_routes as routes


def endpoint(method, path, manager=None):
    return next(r.endpoint for r in routes.setup_skills_routes(manager).routes
                if method in r.methods and r.path == '/api/skills' + path)


def request(owner='alice', body=None):
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(body or {}).encode()}
    return Request({'type': 'http', 'method': 'POST', 'path': '/',
                    'headers': [(b'content-type', b'application/json')],
                    'state': {'user': owner}}, receive)


def test_api_and_python_signatures_match_baseline():
    baseline = json.loads((Path(__file__).parent / 'fixtures/skills_api_baseline.json').read_text())
    app = FastAPI()
    router = routes.setup_skills_routes(object())
    app.include_router(router)
    assert hashlib.sha256(json.dumps(app.openapi(), sort_keys=True).encode()).hexdigest() == baseline['openapi_sha256']
    declarations = [[sorted(r.methods), r.path, r.name] for r in router.routes]
    assert declarations == baseline['routes']
    for name, signature in baseline['signatures'].items():
        assert str(inspect.signature(getattr(routes, name))) == signature, name


@pytest.mark.parametrize('raw, expected', [
    ('<think>{"verdict":"fail"}</think>{"verdict":"PASS","confidence":2,}', 'pass'),
    ('{"verdict":"fail"} text {"verdict":"needs_work"}', 'needs_work'),
    ('Verdict: INCONCLUSIVE', 'inconclusive'),
    ('<think>{"verdict":"pass"}', 'unknown'),
    ('{"verdict":"invalid"}', 'unknown'),
])
async def test_run_verdict_parsing_and_retry(monkeypatch, raw, expected):
    calls = []
    async def call(url, model, messages, **kwargs):
        calls.append((messages, kwargs))
        return raw
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    monkeypatch.setattr(routes, 'effort_for_call', lambda url, model, owner: 'high' if owner == 'alice' else None)
    result = await routes._eval_skill_run('md', 'task', 'transcript', 'url', 'm', {},
                                          workload='background', owner='alice')
    assert result['verdict'] == expected
    assert len(calls) == (2 if expected == 'unknown' else 1)
    for _, kwargs in calls:
        assert kwargs['reasoning_effort'] == 'high'
        assert kwargs['workload'] == 'background'
        assert kwargs['timeout'] == 180
        assert kwargs['max_tokens'] == 32768
    if len(calls) == 2:
        assert "must START with '{'" in calls[1][0][0]['content']


async def test_review_transient_failure_and_result_coercion(monkeypatch):
    calls = []
    async def call(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError('offline')
        return json.dumps({'verdict': 'pass', 'confidence': '-1', 'summary': 'x' * 500,
                           'issues': ['y' * 250] * 10, 'baseline_verdict': 'invalid',
                           'usefulness': '2', 'saved_turns': '-2', 'saved_tool_calls': 'bad'})
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    result = await routes._eval_skill_run('', '', '', 'url', 'm', None)
    assert result == {'verdict': 'pass', 'confidence': 0.0, 'summary': 'x' * 400,
                      'issues': ['y' * 200] * 8, 'baseline_verdict': 'unknown',
                      'usefulness': 1.0, 'saved_turns': -2, 'saved_tool_calls': 0}


async def test_review_failure_schema_and_transcript_tail(monkeypatch):
    seen = []
    async def call(url, model, messages, **kwargs):
        seen.append(messages)
        raise RuntimeError('offline')
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    result = await routes._eval_skill_run('m' * 5000, 'TASK', 'HEAD' + 'x' * 30000 + 'TAIL', 'url', 'm', None)
    assert result == {'verdict': 'unknown', 'confidence': 0,
                      'summary': 'Review call failed: offline', 'issues': []}
    assert len(seen) == 2
    assert 'HEAD' in seen[0][1]['content'] and 'TAIL' in seen[0][1]['content']
    assert 'm' * 4001 not in seen[0][1]['content']


@pytest.mark.parametrize('name, raw, expected, tokens, timeout', [
    ('_eval_skill_necessity', '<think>x</think>{"necessary":false,"redundant_with":["demo"],"reason":"duplicate",}',
     {'necessary': False, 'redundant_with': ['demo'], 'reason': 'duplicate'}, 8192, 120),
    ('_eval_skill_retrieval_precision', '{"ok":false,"summary":"broad","issues":["metadata: retrieval: narrow"]}',
     {'ok': False, 'summary': 'broad', 'issues': ['metadata: retrieval: narrow']}, 4096, 90),
    ('_eval_skill_necessity', '<think>unfinished', None, 8192, 120),
    ('_eval_skill_retrieval_precision', '[]', None, 4096, 90),
])
async def test_advisory_schema_budget_and_owner(monkeypatch, name, raw, expected, tokens, timeout):
    async def call(*args, **kwargs):
        assert kwargs['max_tokens'] == tokens
        assert kwargs['timeout'] == timeout
        assert kwargs['reasoning_effort'] == 'medium'
        return raw
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    monkeypatch.setattr(routes, 'effort_for_call', lambda url, model, owner: 'medium' if owner == 'alice' else None)
    assert await getattr(routes, name)('md', [], 'url', 'm', None, owner='alice') == expected


@pytest.mark.parametrize('name', ['_eval_skill_necessity', '_eval_skill_retrieval_precision', '_improve_skill_md'])
async def test_advisory_exceptions_return_none(monkeypatch, name):
    async def call(*args, **kwargs):
        raise TimeoutError('offline')
    monkeypatch.setattr('src.llm_core.llm_call_async', call)
    args = ('md', {}, '', 'url', 'm', None) if name == '_improve_skill_md' else ('md', [], 'url', 'm', None)
    assert await getattr(routes, name)(*args) is None


async def test_cancelled_audit_closes_stream_in_originating_context(monkeypatch):
    bound = ContextVar('audit_characterization', default=None)
    opened = asyncio.Event()
    closed = []
    async def stream(*args, **kwargs):
        token = bound.set('bound')
        try:
            yield 'data: {"delta":"started"}\n\n'
            opened.set()
            await asyncio.Event().wait()
        finally:
            bound.reset(token)
            closed.append(asyncio.current_task())
    monkeypatch.setattr('src.agent_loop.stream_agent_loop', stream)
    task = asyncio.create_task(routes._run_skill_audit_arm([], 'url', 'm', {}, 'alice'))
    await opened.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [task]
    assert bound.get() is None


async def test_cancelled_batch_skips_finalization_and_cleans_task(monkeypatch):
    opened = asyncio.Event()
    async def audit(*args, **kwargs):
        opened.set()
        await asyncio.Event().wait()
    class Manager:
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': 'demo'}]
    job = {'status': 'running', 'log': [], 'results': [], 'done': 0}
    monkeypatch.setattr(routes, '_skill_audit_jobs', {('alice',): job})
    monkeypatch.setattr(routes, '_audit_one_skill', audit)
    monkeypatch.setattr(routes, '_finalize_audit_batch', lambda *args: pytest.fail('cancelled batch finalized'))
    task = asyncio.create_task(routes._run_audit_all_job(('alice',), Manager(), ['demo'], 'url', 'm', {}, None, 'alice'))
    job['task'] = task
    await opened.wait()
    task.cancel()
    await task
    assert job['status'] == 'cancelled'
    assert job['current'] is None and 'task' not in job and 'finished' in job
    assert job['results'] == [] and job['done'] == 0


async def test_test_polling_is_owner_scoped_and_hides_continuation(monkeypatch):
    class Manager:
        def load(self, owner=None):
            return [{'name': 'demo'}]
    monkeypatch.setattr(routes, 'get_current_user', lambda req: req.state.user)
    job = {'status': 'awaiting_approval', 'task': 'task', 'model': 'm', 'log': [],
           'verdict': None, 'approval': {'approval_id': 'id'},
           '_run': {'headers': {'Authorization': 'secret'}}, '_transcript': ['private']}
    monkeypatch.setattr(routes, '_skill_test_jobs', {('alice', 'demo'): job})
    poll = endpoint('GET', '/{skill_id}/test-status', Manager())
    assert await poll(request('bob'), 'demo') == {'status': 'none'}
    assert await poll(request(), 'demo') == {k: job[k] for k in ('status', 'task', 'model', 'log', 'verdict', 'approval')}


def test_batch_finalizer_protects_approval_required_and_unprocessed_skills(monkeypatch):
    class Manager:
        def __init__(self):
            self.updated = []
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': name, 'status': 'published', 'source': 'learned',
                     'audit_verdict': 'fail', 'confidence': .9} for name in ('demo', 'demo-1', 'demo-2')]
        def update_skill(self, name, fields, owner=None):
            self.updated.append((name, fields, owner))
    monkeypatch.setattr(routes, '_audit_auto_publish_policy', lambda owner: (True, .8))
    manager = Manager()
    routes._finalize_audit_batch(manager, [{'skill': 'demo', 'result': 'approval_required'},
                                          {'skill': 'demo-1', 'result': 'flagged'}], 'alice', lambda msg: None)
    assert manager.updated == [('demo-1', {'status': 'draft'}, 'alice')]
