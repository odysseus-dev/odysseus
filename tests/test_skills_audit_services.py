"""Service and HTTP integration contracts without live model or skill state."""
import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException

from routes import skills_routes as routes
from services.skill_audit import evaluation, execution, jobs, models, orchestration, policy
from src.agent_runtime.authority import RequestAuthority, bind_request_authority
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import capabilities_for_action
from tests.test_skills_audit_characterization import endpoint, request


async def test_evaluation_can_inject_model_call_and_owner_effort_without_network():
    seen = []
    async def call(url, model, messages, **kwargs):
        seen.append(kwargs)
        return '{"verdict":"pass"}'
    def effort(url, model, owner):
        assert (url, model, owner) == ('offline', 'worker', 'alice')
        return 'high'
    result = await evaluation.evaluate_run('md', 'task', 'done', 'offline', 'worker', None,
                                           owner='alice', _llm_call=call, _effort_for_call=effort)
    assert result['verdict'] == 'pass'
    assert seen[0]['reasoning_effort'] == 'high'


async def test_rewrite_keeps_last_document_and_owner_effort():
    async def call(*args, **kwargs):
        assert kwargs['reasoning_effort'] == 'medium'
        return '<think>reasoning</think>old\n---\nname: old\n---\nbody\n---\nname: demo\n---\nfixed'
    assert await evaluation.improve_markdown('md', {}, 'transcript', 'url', 'm', {},
        owner='alice', _llm_call=call, _effort_for_call=lambda u, m, o: 'medium') == '---\nname: demo\n---\nfixed'


def test_audit_model_resolution_uses_first_owner_scoped_utility_fallback(monkeypatch):
    seen = []
    def primary(role, owner=None):
        seen.append(('primary', role, owner))
        return None, None, {}
    def fallback(owner=None):
        seen.append(('fallback', owner))
        return [('first', '/models/worker', {'Authorization': 'owner-key'}), ('second', 'other', {})]
    def served(url, headers=None):
        assert (url, headers) == ('first', {'Authorization': 'owner-key'})
        return ['worker']
    monkeypatch.setattr('src.endpoint_resolver.resolve_endpoint', primary)
    monkeypatch.setattr('src.endpoint_resolver.resolve_utility_fallback_candidates', fallback)
    monkeypatch.setattr('src.llm_core.list_model_ids', served)
    monkeypatch.setattr('src.settings.get_setting', lambda key, default=None: default)
    assert models.resolve_audit_models(owner='alice') == ('first', 'worker', {'Authorization': 'owner-key'}, None)
    assert seen == [('primary', 'utility', 'alice'), ('fallback', 'alice')]


async def test_audit_stream_failure_closes_and_restores_inherited_authority(monkeypatch):
    authority = RequestAuthority.empty(owner='alice')
    closed = []
    async def stream(*args, **kwargs):
        assert kwargs['owner'] == 'alice'
        assert kwargs['request_authority'] is authority
        assert kwargs['suppress_skills'] is True
        try:
            yield 'event: error\ndata: {"error":"offline"}\n\n'
        finally:
            closed.append(asyncio.current_task())
    monkeypatch.setattr('src.agent_loop.stream_agent_loop', stream)
    with bind_request_authority(authority):
        with pytest.raises(execution.SkillAuditUnavailable, match='offline'):
            await execution.run_audit_arm([], 'url', 'm', {}, 'alice')
    assert closed == [asyncio.current_task()]


async def test_manual_exception_closes_stream_and_finishes_without_publishing(monkeypatch):
    closed = []
    async def stream(*args, **kwargs):
        try:
            yield 'data: {"delta":"partial"}\n\n'
            raise RuntimeError('stream failed')
        finally:
            closed.append(asyncio.current_task())
    async def arm(*args, **kwargs):
        return 'baseline', {}, None
    async def evaluate(*args, **kwargs):
        return {'verdict': 'fail', 'summary': 'failed', 'issues': []}
    class Manager:
        def __init__(self):
            self.updates = []
        def set_audit(self, name, verdict, **kwargs):
            assert name == 'demo' and verdict == 'fail' and kwargs['owner'] == 'alice'
        def update_skill(self, name, fields, owner=None):
            self.updates.append((fields, owner))
    job = {'status': 'running', 'log': [], '_run': {}, '_transcript': []}
    deps = replace(routes._audit_dependencies(), test_jobs={('alice', 'demo'): job},
                   run_arm=arm, evaluate_run=evaluate)
    monkeypatch.setattr('src.agent_loop.stream_agent_loop', stream)
    manager = Manager()
    await execution.run_test_job(('alice', 'demo'), 'demo', 'md', 'task', 'url', 'm', {},
                                 'alice', manager, deps=deps)
    assert closed == [asyncio.current_task()]
    assert job['status'] == 'done' and '_run' not in job and '_transcript' not in job
    assert {'type': 'error', 'error': 'stream failed'} in job['log']
    assert manager.updates == [({'confidence': .4}, 'alice')]


async def test_batch_exception_records_progress_and_finalizes_once():
    class Manager:
        def __init__(self):
            self.audit = []
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': name, 'status': 'draft'} for name in ('first', 'second')]
        def set_audit(self, *args, **kwargs):
            self.audit.append((args, kwargs))
    async def audit(manager, skill, *args, **kwargs):
        if skill['name'] == 'first':
            raise RuntimeError('failed')
        return {'skill': 'second', 'result': 'pass'}
    finalized = []
    job = {'status': 'running', 'log': [], 'results': [], 'done': 0, 'task': object()}
    deps = replace(routes._audit_dependencies(), audit_jobs={('alice',): job}, audit_one=audit,
                   finalize_batch=lambda manager, results, owner, log: finalized.append((list(results), owner)))
    manager = Manager()
    await orchestration.run_audit_batch(('alice',), manager, ['first', 'second'], 'url', 'm', {}, None, 'alice', deps=deps)
    assert job['status'] == 'done' and job['done'] == 2 and job['current'] is None
    assert 'task' not in job and 'finished' in job
    assert [r['result'] for r in job['results']] == ['error', 'pass']
    assert manager.audit == [(('first', 'inconclusive'), {'worker_model': 'm', 'owner': 'alice'})]
    assert finalized == [(job['results'], 'alice')]


@pytest.mark.parametrize('enabled,confidence,verdict,expected', [
    (True, .85, 'pass', 'published'), (True, .84, 'pass', 'draft'),
    (False, .95, 'pass', 'draft'), (True, .95, 'fail', 'draft'),
    (False, .2, 'unknown', 'published'),
])
def test_policy_preserves_threshold_and_auto_approval(enabled, confidence, verdict, expected):
    class Manager:
        def __init__(self):
            self.updates = []
        def load(self, owner=None):
            return [{'name': 'demo', 'status': 'published'}]
        def update_skill(self, name, fields, owner=None):
            assert owner == 'alice'
            self.updates.append(fields)
    deps = replace(routes._audit_dependencies(), auto_publish_policy=lambda owner: (enabled, .85),
                   duplicate_blocker=lambda *args: None)
    manager = Manager()
    assert policy.finalize_status(manager, 'demo', 'alice', verdict, confidence, deps=deps) == expected
    assert manager.updates == ([] if verdict == 'unknown' else [{'status': expected}])


@pytest.mark.parametrize('enabled', [True, False])
def test_batch_duplicate_finalization_changes_only_processed_unprotected_skills(enabled):
    class Manager:
        def __init__(self):
            self.updates = []
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': name, 'source': 'learned', 'status': 'draft',
                     'audit_verdict': 'pass', 'confidence': .95, 'uses': uses}
                    for name, uses in [('demo', 10), ('demo-1', 0), ('demo-2', 20), ('demo-3', 30)]]
        def update_skill(self, name, fields, owner=None):
            self.updates.append((name, fields, owner))
    deps = replace(routes._audit_dependencies(), auto_publish_policy=lambda owner: (enabled, .85))
    manager = Manager()
    policy.finalize_batch(manager, [{'skill': 'demo', 'result': 'pass'},
                                    {'skill': 'demo-1', 'result': 'pass'},
                                    {'skill': 'demo-3', 'result': 'approval_required'}],
                          'alice', lambda message: None, deps=deps)
    expected = [('demo', {'status': 'published'}, 'alice')] if enabled else []
    assert manager.updates == expected + [('demo-1', {'status': 'binned'}, 'alice')]


@pytest.fixture
def approval_route(monkeypatch):
    store = ToolApprovalStore()
    monkeypatch.setattr('src.tool_approvals.tool_approval_store', store)
    monkeypatch.setattr(routes, 'get_current_user', lambda req: req.state.user)
    monkeypatch.setattr(routes, '_skill_test_jobs', {})
    class Manager:
        def load(self, owner=None):
            return [{'name': 'demo', 'owner': owner}]
    pending = store.create(owner='alice', session_id=None, origin_run_id='run', tool_name='bash',
        content='printf approved', workspace=None, external_untrusted_context_seen=True,
        capabilities=capabilities_for_action('bash', 'printf approved'))
    job = {'status': 'awaiting_approval', 'log': [], 'task': 'task',
           'approval': pending.public_payload(), '_transcript': ['untrusted result'],
           '_run': {'owner': 'alice', 'md': 'untrusted skill', 'url': 'url', 'model': 'm'}}
    routes._skill_test_jobs[('alice', 'demo')] = job
    return endpoint('POST', '/{skill_id}/test-approval', Manager()), store, pending, job


async def test_denied_skill_approval_retires_state_without_spawning(approval_route, monkeypatch):
    approve, store, pending, job = approval_route
    async def run(*args, **kwargs):
        pytest.fail('denial spawned a test')
    monkeypatch.setattr(routes, '_run_skill_test_job', run)
    result = await approve(request(body={'approval_id': pending.approval_id, 'decision': 'deny'}), 'demo')
    assert result == {'ok': True, 'status': 'done', 'decision': 'deny'}
    assert store.peek(pending.approval_id) is None
    assert job['verdict']['verdict'] == 'inconclusive'
    assert not {'approval', '_run', '_transcript'} & job.keys()


@pytest.mark.parametrize('case,status', [('other_owner', 409), ('mismatch', 409), ('consumed', 409), ('invalid_decision', 400)])
async def test_invalid_skill_approval_cannot_resume(approval_route, monkeypatch, case, status):
    approve, store, pending, job = approval_route
    async def run(*args, **kwargs):
        pytest.fail('invalid approval resumed a test')
    monkeypatch.setattr(routes, '_run_skill_test_job', run)
    owner = 'bob' if case == 'other_owner' else 'alice'
    if case == 'other_owner':
        routes._skill_test_jobs[('bob', 'demo')] = job
    if case == 'consumed':
        store.consume(pending.approval_id, decision='deny', owner='alice', session_id=None)
    body = {'approval_id': 'wrong' if case == 'mismatch' else pending.approval_id,
            'decision': 'approve_task' if case == 'invalid_decision' else 'approve'}
    with pytest.raises(HTTPException) as error:
        await approve(request(owner, body), 'demo')
    assert error.value.status_code == status
    assert job['status'] == 'awaiting_approval'


async def test_allowed_skill_approval_is_single_action_and_cannot_be_reused(approval_route, monkeypatch):
    approve, store, pending, job = approval_route
    resumed = []
    async def run(*args, **kwargs):
        resumed.append((args, kwargs))
    monkeypatch.setattr(routes, '_run_skill_test_job', run)
    body = {'approval_id': pending.approval_id, 'decision': 'approve'}
    assert await approve(request(body=body), 'demo') == {'ok': True, 'status': 'running', 'decision': 'approve'}
    await asyncio.sleep(0)
    assert len(resumed) == 1 and resumed[0][0][7] == 'alice'
    grant = resumed[0][1]['exact_approval']
    assert grant.pending is pending and grant.allow_remaining_actions is False
    assert grant.grants_chat_session is False
    assert store.peek(pending.approval_id) is None
    messages = resumed[0][1]['messages']
    tainted = [m for m in messages if m.get('metadata', {}).get('tool_gate_untrusted')]
    assert len(tainted) == 2
    assert any('untrusted result' in m['content'] for m in tainted)
    with pytest.raises(HTTPException) as error:
        await approve(request(body=body), 'demo')
    assert error.value.status_code == 409
    assert len(resumed) == 1


@pytest.mark.parametrize('scope,selected,skip,names,expected_scope', [
    ('all', None, False, ['draft', 'published', 'checked'], 'all'),
    ('drafts', None, False, ['draft'], 'unchecked'),
    ('all', ['published', 'draft', 'draft', 'builtin', 'missing'], False, ['published', 'draft'], 'selected'),
    ('all', None, True, ['draft', 'published'], 'all'),
])
def test_manual_audit_selection_preserves_published_and_builtin_rules(scope, selected, skip, names, expected_scope):
    class Manager:
        def load(self, owner=None):
            assert owner == 'alice'
            return [{'name': 'draft', 'status': 'draft'}, {'name': 'published', 'status': 'published'},
                    {'name': 'checked', 'audit_verdict': 'pass'}, {'name': 'builtin', 'source': 'builtin'}]
    assert jobs.select_audit_names(Manager(), 'alice', scope, selected, skip) == (names, expected_scope)
