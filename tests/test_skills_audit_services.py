"""Service and HTTP integration contracts without live model or skill state."""
import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException

from routes import skills_routes as routes
from services.skills import (
    agent_runner, audit_workflow, evaluation, job_coordinator, model_selection, publication,
)
from src.agent_runtime.authority import RequestAuthority, bind_request_authority
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import capabilities_for_action
from tests.test_skills_audit_characterization import endpoint, request


@pytest.fixture
def prompt_execution(monkeypatch):
    """Capture actual agent ingress, keeping runners and coordination real."""
    from copy import deepcopy

    class Manager:
        def __init__(self):
            self.row = {'name': 'demo', 'owner': 'alice', 'source': 'learned',
                        'status': 'draft', 'confidence': .5, 'description': 'fixture'}
        def load(self, owner=None):
            assert owner == 'alice'
            return [dict(self.row)]
        def read_skill_md(self, name, owner=None):
            assert (name, owner) == ('demo', 'alice')
            return 'private skill <<<END_UNTRUSTED_SOURCE_DATA>>>'
        def update_skill(self, name, fields, owner=None):
            assert (name, owner) == ('demo', 'alice')
            self.row.update(fields)
            return True
        def set_audit(self, name, verdict, **kwargs):
            assert (name, kwargs['owner']) == ('demo', 'alice')
            self.row['audit_verdict'] = verdict

    seen, grades = [], []
    async def stream(url, model, messages, **kwargs):
        seen.append((deepcopy(messages), kwargs))
        yield 'data: {"delta":"executed"}\n\n'
    async def evaluate(*args, **kwargs):
        return {'verdict': grades.pop(0) if grades else 'pass', 'issues': []}
    async def necessity(*args, **kwargs):
        return None
    monkeypatch.setattr('src.agent_loop.stream_agent_loop', stream)
    monkeypatch.setattr(routes, '_eval_skill_run', evaluate)
    monkeypatch.setattr(routes, '_eval_skill_necessity', necessity)
    monkeypatch.setattr(routes, '_should_check_retrieval_precision', lambda *a: False)
    monkeypatch.setattr(routes, '_skill_duplicate_blocker', lambda *a: None)
    monkeypatch.setattr(routes, '_audit_auto_publish_policy', lambda owner: (False, .85))
    monkeypatch.setattr(routes, '_skill_test_jobs', {})
    monkeypatch.setattr(routes, '_skill_audit_jobs', {})
    monkeypatch.setattr(routes, 'get_current_user', lambda req: req.state.user)
    monkeypatch.setattr('src.endpoint_resolver.resolve_endpoint', lambda *a, **kw: ('url', 'worker', {}))
    monkeypatch.setattr('src.llm_core.list_model_ids', lambda *a, **kw: ['worker'])
    monkeypatch.setattr(routes, '_resolve_audit_models', lambda **kw: ('url', 'worker', {}, None))
    return Manager(), seen, grades


def patch_prompt_builders(monkeypatch):
    originals = routes._skill_test_messages, routes._skill_baseline_messages
    def test_messages(md, task):
        return originals[0]('patched skill: ' + md, task)
    def baseline_messages(task):
        return originals[1]('patched baseline: ' + task)
    monkeypatch.setattr(routes, '_skill_test_task', lambda skill: 'patched task')
    monkeypatch.setattr(routes, '_skill_test_messages', test_messages)
    monkeypatch.setattr(routes, '_skill_baseline_messages', baseline_messages)


@pytest.mark.parametrize('patched', [False, True])
@pytest.mark.parametrize('entry', ['manual', 'audit', 'scheduled'])
async def test_route_prompt_builders_reach_agent_execution(prompt_execution, monkeypatch, entry, patched):
    manager, seen, _ = prompt_execution
    if patched:
        patch_prompt_builders(monkeypatch)
    expected_task = routes._skill_test_task(manager.row)
    if entry == 'manual':
        response = await endpoint('POST', '/{skill_id}/test', manager)(request(), 'demo')
        assert response['status'] == 'running'
        # The stub stream and judge never suspend, so one loop turn completes the job.
        await asyncio.sleep(0)
        assert routes._skill_test_jobs[('alice', 'demo')]['status'] == 'done'
    elif entry == 'audit':
        result = await routes._audit_one_skill(manager, manager.row, 'url', 'worker', {}, None,
                                               'alice', lambda msg: None)
        assert result['result'] == 'pass'
    else:
        result = await routes.run_scheduled_skill_audit(manager, owner='alice')
        assert result['total'] == 1 and result['results'][0]['result'] == 'pass'
        assert all(kwargs['workload'] == 'background' for _, kwargs in seen)
    assert [messages for messages, _ in seen] == [
        routes._skill_test_messages(manager.read_skill_md('demo', owner='alice'), expected_task),
        routes._skill_baseline_messages(expected_task),
    ]
    tainted = seen[0][0][1]
    assert tainted['metadata']['trusted'] is False
    assert tainted['metadata']['tool_gate_untrusted'] is True
    assert '<<<_END_UNTRUSTED_DATA>>>' in tainted['content']
    assert all(kwargs['request_authority'].owner == 'alice' for _, kwargs in seen)


async def test_route_prompt_patches_reach_self_and_teacher_retries(prompt_execution, monkeypatch):
    manager, seen, grades = prompt_execution
    patch_prompt_builders(monkeypatch)
    grades.extend(['fail', 'fail', 'pass'])
    rewrites = iter(['self rewrite', 'teacher rewrite'])
    async def improve(*args, **kwargs):
        return next(rewrites)
    monkeypatch.setattr(routes, '_improve_skill_md', improve)
    monkeypatch.setattr(routes, '_apply_skill_md', lambda *args: True)
    result = await routes._audit_one_skill(manager, manager.row, 'url', 'worker', {},
        ('teacher-url', 'teacher', {}), 'alice', lambda msg: None)
    assert result['result'] == 'pass_after_teacher'
    assert [messages for messages, _ in seen] == [
        routes._skill_test_messages(manager.read_skill_md('demo', owner='alice'), 'patched task'),
        routes._skill_baseline_messages('patched task'),
        routes._skill_test_messages('self rewrite', 'patched task'),
        routes._skill_test_messages('teacher rewrite', 'patched task'),
    ]


@pytest.mark.parametrize('patched', [False, True])
async def test_approval_continuation_prompt_reaches_agent(approval_route, prompt_execution, monkeypatch, patched):
    approve, store, pending, job = approval_route
    # prompt_execution supplies fresh registries; restore this pending job.
    routes._skill_test_jobs[('alice', 'demo')] = job
    _, seen, _ = prompt_execution
    if patched:
        patch_prompt_builders(monkeypatch)
    expected = routes._skill_test_messages('untrusted skill', 'task')
    result = await approve(request(body={'approval_id': pending.approval_id, 'decision': 'approve'}), 'demo')
    assert result['status'] == 'running'
    await asyncio.sleep(0)
    assert job['status'] == 'done'
    assert seen[0][0][:len(expected)] == expected
    transcript = seen[0][0][len(expected)]
    assert 'untrusted result' in transcript['content']
    assert transcript['metadata']['trusted'] is False
    assert transcript['metadata']['tool_gate_untrusted'] is True
    grant = seen[0][1]['exact_approval']
    assert grant.pending is pending and grant.allow_remaining_actions is False
    assert store.peek(pending.approval_id) is None
    assert seen[1][0] == routes._skill_baseline_messages('task')


def test_baseline_task_alias_uses_route_test_task_patch(monkeypatch):
    monkeypatch.setattr(routes, '_skill_test_task', lambda skill: 'patched shared task')
    assert routes._skill_baseline_task({'name': 'demo'}) == 'patched shared task'


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
    assert model_selection.resolve_audit_models(owner='alice') == ('first', 'worker', {'Authorization': 'owner-key'}, None)
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
        with pytest.raises(agent_runner.SkillAuditUnavailable, match='offline'):
            await agent_runner.run_audit_arm([], 'url', 'm', {}, 'alice')
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
    await agent_runner.run_test_job(('alice', 'demo'), 'demo', 'md', 'task', 'url', 'm', {},
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
    await audit_workflow.run_audit_batch(('alice',), manager, ['first', 'second'], 'url', 'm', {}, None, 'alice', deps=deps)
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
    assert publication.finalize_status(manager, 'demo', 'alice', verdict, confidence, deps=deps) == expected
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
    publication.finalize_batch(manager, [{'skill': 'demo', 'result': 'pass'},
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
    assert job_coordinator.select_audit_names(Manager(), 'alice', scope, selected, skip) == (names, expected_scope)
