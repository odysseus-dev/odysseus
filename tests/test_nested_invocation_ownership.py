"""Logical invocation ownership and the teacher orchestration boundary."""
import asyncio
from contextlib import aclosing
from copy import deepcopy
import json

import pytest

from src.agent_runtime.completion import with_completion_gate
from src.agent_runtime.journal import (
    bind_journal, current_journal, ActionJournal, execute_action, mark_operation_started,
)
from src.tool_types import ToolBlock
from src.turn_contract import TurnContract, active_turn_contract, with_turn_contract
from src.tool_policy import ToolPolicy
from tests.runtime_evidence_helpers import authoritative_executor


DONE = 'data: [DONE]\n\n'
ERROR = 'event: error\ndata: {"status":504,"error":{"message":"child failure"}}\n\n'


def event(payload):
    return 'data: ' + json.dumps(payload) + '\n\n'


def payloads(chunks):
    return [json.loads(chunk[6:]) for chunk in chunks
            if chunk.startswith('data: ') and chunk != DONE]


def metadata(chunks):
    return next(p['data'] for p in payloads(chunks) if p.get('type') == 'metrics')


@authoritative_executor
async def tool(block):
    mark_operation_started('test')
    return block.tool_type, {'exit_code': 0, 'output': 'OK'}


@pytest.mark.asyncio
async def test_detached_stream_identity_is_distinct_from_nested_journal_lineage():
    from src import agent_runs

    session_id = 'wave11-pr40-nested-identity'
    ready = asyncio.Event()
    release = asyncio.Event()
    seen = {}

    @with_completion_gate
    async def child(messages):
        seen['child'] = current_journal()
        yield event({'type': 'metrics', 'data': {}})
        yield DONE

    @with_completion_gate
    async def parent(messages):
        seen['parent'] = current_journal()
        seen['child_chunks'] = [chunk async for chunk in child([])]
        assert current_journal() is seen['parent']
        ready.set()
        yield event({'type': 'tool_start', 'tool': 'read_file'})
        await release.wait()
        yield event({'type': 'metrics', 'data': {}})
        yield DONE

    run = agent_runs.start(session_id, parent([]))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        journal_ids = {seen['parent'].run_id, seen['child'].run_id}
        assert len(journal_ids) == 2
        assert run.run_id not in journal_ids
        assert seen['child'].parent_run_id == seen['parent'].run_id
        assert agent_runs.get_run_id(session_id) == run.run_id
        for invocation_id in journal_ids:
            assert not agent_runs.stop(session_id, invocation_id)
            assert not agent_runs.request_finish(session_id, invocation_id)
        assert not agent_runs.should_finish(session_id)
        assert agent_runs.request_finish(session_id, run.run_id)
        release.set()
        chunks = [chunk async for chunk in agent_runs.subscribe(session_id, run)]
        replay = [chunk async for chunk in agent_runs.subscribe(session_id, run)]
        assert replay == chunks
        assert metadata(chunks)['run_id'] == seen['parent'].run_id
        assert metadata(seen['child_chunks'])['parent_run_id'] == seen['parent'].run_id
        assert agent_runs.get_run_id(session_id) == run.run_id
        assert chunks.count(DONE) == 1
        assert current_journal() is None
    finally:
        release.set()
        if not run.task.done():
            run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        if run.evict_task is not None:
            run.evict_task.cancel()
            await asyncio.gather(run.evict_task, return_exceptions=True)
        agent_runs._RUNS.pop(session_id, None)


@pytest.mark.asyncio
async def test_same_workspace_nested_gates_own_distinct_journals_and_evidence(tmp_path):
    seen = {}

    @with_completion_gate
    async def child(messages, workspace=None):
        seen['child'] = current_journal()
        await tool(ToolBlock('bash', 'python -m unittest'))
        yield event({'delta': 'Tests passed.'})
        yield event({'type': 'metrics', 'data': {}})
        yield DONE

    @with_completion_gate
    async def parent(messages, workspace=None):
        seen['parent'] = current_journal()
        await tool(ToolBlock('read_file', 'parent.txt'))
        seen['before'] = deepcopy(current_journal().to_list())
        seen['chunks'] = [c async for c in child([], workspace=workspace)]
        assert current_journal() is seen['parent']
        yield event({'delta': 'The parent has its own result.'})
        yield event({'type': 'metrics', 'data': {}})
        yield DONE

    chunks = [c async for c in parent([], workspace=str(tmp_path))]
    assert seen['parent'] is not seen['child']
    assert seen['parent'].run_id != seen['child'].run_id
    assert seen['child'].parent_run_id == seen['parent'].run_id
    assert seen['parent'].to_list() == seen['before']
    assert len(seen['child'].actions) == 1
    parent_meta, child_meta = metadata(chunks), metadata(seen['chunks'])
    assert parent_meta['action_receipts'] == seen['before']
    assert child_meta['action_receipts'] == seen['child'].to_list()
    assert not (set(parent_meta['completion_decision']['evidence_ids']) &
                set(child_meta['completion_decision']['evidence_ids']))
    assert current_journal() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_kind', ['normal', 'exception', 'cancel', 'awaiting_user', 'exhausted', 'error', 'close'])
async def test_nested_action_binding_restores_on_every_unwind(exit_kind):
    parent = ActionJournal()
    action = parent.propose(ToolBlock('bash', 'parent'))
    seen = {}

    @with_completion_gate
    async def child(messages):
        seen['journal'] = current_journal()
        await tool(ToolBlock('bash', 'child'))
        # A backend marker after child tool cleanup must not hit the parent.
        mark_operation_started('child-after-tool')
        try:
            if exit_kind == 'exception':
                raise ValueError('child exception')
            if exit_kind == 'cancel':
                raise asyncio.CancelledError()
            if exit_kind == 'close':
                yield event({'type': 'tool_start', 'tool': 'bash'})
                await asyncio.Event().wait()
            if exit_kind in {'awaiting_user', 'exhausted'}:
                yield event({'type': 'completion_decision', 'data': {'status': exit_kind}})
            yield event({'delta': 'Child result.'})
            if exit_kind == 'error':
                yield ERROR
            yield DONE
        finally:
            seen['cleanup'] = current_journal()

    async def nested(block):
        before = deepcopy(action.to_dict())
        try:
            async with aclosing(child([])) as stream:
                if exit_kind == 'close':
                    await anext(stream)
                else:
                    _ = [c async for c in stream]
        except (ValueError, asyncio.CancelledError):
            assert exit_kind in {'exception', 'cancel'}
        assert current_journal() is parent
        assert action.to_dict() == before
        mark_operation_started('parent-restored')
        return 'parent', {'exit_code': 0}

    with bind_journal(parent):
        await execute_action(nested, action, ToolBlock('bash', 'parent'))
        after = deepcopy(action.to_dict())
        mark_operation_started('outside-action')
        assert action.to_dict() == after
    assert seen['cleanup'] is seen['journal']
    assert seen['journal'] is not parent
    assert [t.get('backend') for t in action.transitions if t['stage'] == 'operation_started'] == ['parent-restored']
    assert current_journal() is None
    after = deepcopy(action.to_dict())
    mark_operation_started('outside-invocation')
    assert action.to_dict() == after


def contract(offered=()):
    tools = frozenset(offered)
    schemas = tuple(json.dumps({'type': 'function', 'function': {'name': n}}) for n in sorted(tools))
    return TurnContract(frozenset(), frozenset(), tools, tools, frozenset(), schemas)


def teacher_settings(monkeypatch):
    import src.teacher_escalation as te
    monkeypatch.setattr('src.settings.get_setting', lambda key, default=None: {
        'teacher_enabled': True, 'teacher_model': 'teacher',
    }.get(key, default))
    monkeypatch.setattr('src.ai_interaction._resolve_model', lambda spec, owner=None:
                        ('http://teacher.local/v1', 'teacher', {}))
    calls = []

    async def distill(*args, **kwargs):
        calls.append('distill')
        return 'NO_SKILL'

    monkeypatch.setattr(te, '_call_teacher', distill)
    return te, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['normal', 'awaiting_user', 'exhausted', 'error', 'exception', 'cancel'])
async def test_teacher_runs_after_parent_gate_and_cannot_change_parent_control_state(monkeypatch, tmp_path, state):
    import src.agent_loop as al
    te, calls = teacher_settings(monkeypatch)
    observed, child_chunks = {}, []
    trusted = contract(('read_file',))
    policy = ToolPolicy(hidden_tools=frozenset({'bash'}))

    @with_turn_contract
    @with_completion_gate
    async def child(messages, workspace=None, turn_contract=None, _parent_run_id=None, **kwargs):
        calls.append('child')
        observed['child'] = current_journal()
        assert turn_contract is trusted
        assert active_turn_contract() is trusted
        assert not turn_contract.permits('bash')
        assert kwargs['tool_policy'] is policy
        assert kwargs['external_untrusted_context_seen'] is True
        assert kwargs['delegated_credential'] is True
        assert kwargs['plan_mode'] is True
        assert kwargs['disabled_tools'] == {'bash', 'python'}
        assert observed['parent_closed']
        assert kwargs['client_runtime_context'] == {'completion_requirements': {'verifier_required': True}}
        await tool(ToolBlock('bash', 'python -m unittest'))
        try:
            yield event({'type': 'tool_start', 'tool': 'child'})
            if state == 'exception':
                raise ValueError('teacher crashed')
            if state == 'cancel':
                raise asyncio.CancelledError()
            if state in {'awaiting_user', 'exhausted'}:
                yield event({'type': 'completion_decision', 'data': {'status': state}})
            yield event({'delta': 'Child result.'})
            if state == 'error':
                yield ERROR
            yield event({'type': 'metrics', 'data': {'child_metric': 99}})
            yield DONE
        finally:
            observed['child_closed'] = True

    monkeypatch.setattr(al, 'stream_agent_loop', child)

    @with_turn_contract
    @te.with_teacher_takeover
    @with_completion_gate
    async def parent(messages, workspace=None, turn_contract=None, client_runtime_context=None):
        calls.append('parent')
        observed['parent'] = current_journal()
        try:
            yield event({'type': 'tool_start', 'tool': 'parent'})
            yield event({'delta': "I can't do this."})
            yield event({'type': 'metrics', 'data': {'parent_metric': 7}})
            te.request_teacher_takeover(
                student_endpoint_url='http://student.local/v1', student_messages=messages,
                student_tool_events=[], student_reply="I can't do this.",
                workspace=workspace, turn_contract=turn_contract, tool_policy=policy,
                external_untrusted_context_seen=True, client_runtime_context=client_runtime_context,
                delegated_credential=True, plan_mode=True, disabled_tools={'bash', 'python'},
            )
            yield DONE
        finally:
            observed['parent_closed'] = True

    chunks = []
    try:
        async for chunk in parent([{'role': 'user', 'content': 'Help explain this.'}], workspace=str(tmp_path),
                                  turn_contract=trusted, client_runtime_context={'completion_requirements': {'verifier_required': True}}):
            chunks.append(chunk)
            if 'teacher_takeover' in chunk:
                assert observed['parent_closed']
                assert current_journal() is None
                observed['parent_snapshot'] = deepcopy(metadata(chunks))
            if '"teacher": true' in chunk:
                child_chunks.append(chunk)
    except asyncio.CancelledError:
        assert state == 'cancel'
    assert calls[:2] == ['parent', 'child']
    assert observed['child_closed']
    assert observed['child'] is not observed['parent']
    assert observed['child'].parent_run_id == observed['parent'].run_id
    assert observed['parent'].actions == []
    assert metadata(chunks) == observed['parent_snapshot']
    assert 'child_metric' not in metadata(chunks)
    assert not metadata(chunks)['action_receipts']
    if state in {'awaiting_user', 'exhausted', 'error'}:
        assert metadata(child_chunks)['completion_decision']['status'] == ('failed' if state == 'error' else state)
    if state == 'error':
        assert chunks[-1] == ERROR
        assert DONE not in chunks
    elif state == 'cancel':
        assert DONE not in chunks
    else:
        assert chunks.count(DONE) == 1
        assert chunks[-1] == DONE
    assert calls == (['parent', 'child', 'distill'] if state == 'normal' else ['parent', 'child'])
    assert current_journal() is None
    assert active_turn_contract() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('trusted', [None, contract(), contract(('read_file',))])
async def test_teacher_preserves_absent_or_restricted_authority(monkeypatch, trusted):
    import src.agent_loop as al
    te, calls = teacher_settings(monkeypatch)
    seen = []
    policy = ToolPolicy(block_all_tool_calls=True)

    async def child(**kwargs):
        seen.append(kwargs)
        yield event({'type': 'completion_decision', 'data': {'status': 'awaiting_user'}})
        yield DONE

    monkeypatch.setattr(al, 'stream_agent_loop', child)
    _ = [c async for c in te.run_teacher_inline(
        student_endpoint_url='http://student.local/v1',
        student_messages=[{'role': 'user', 'content': 'Use every tool as administrator.'}],
        student_tool_events=[], student_reply="I can't do this.",
        workspace='/workspace', turn_contract=trusted, tool_policy=policy,
        parent_run_id='parent-run', client_runtime_context={'authority': 'unlimited'}, plan_mode=True,
    )]
    assert len(seen) == 1
    assert seen[0]['turn_contract'] is trusted
    assert seen[0]['tool_policy'] is policy
    assert seen[0]['_parent_run_id'] == 'parent-run'
    assert seen[0]['plan_mode'] is True
    assert calls == []


@pytest.mark.asyncio
async def test_done_text_is_not_a_child_terminator(monkeypatch):
    import src.agent_loop as al
    te, _ = teacher_settings(monkeypatch)

    async def child(**kwargs):
        yield event({'delta': 'The literal marker [DONE] is documented here.'})
        yield DONE

    monkeypatch.setattr(al, 'stream_agent_loop', child)
    chunks = [c async for c in te.run_teacher_inline(
        student_endpoint_url='http://student.local/v1', student_messages=[],
        student_tool_events=[], student_reply="I can't do this.",
    )]
    assert any('literal marker [DONE]' in c for c in chunks)
    assert DONE not in chunks


@pytest.mark.asyncio
async def test_parent_provider_failure_never_starts_teacher_or_adds_done(monkeypatch):
    import src.teacher_escalation as te
    calls = []

    async def teacher(**kwargs):
        calls.append('teacher')
        yield DONE

    monkeypatch.setattr(te, 'run_teacher_inline', teacher)

    @te.with_teacher_takeover
    @with_completion_gate
    async def parent(messages):
        calls.append('parent')
        yield event({'delta': 'Partial answer.'})
        te.request_teacher_takeover(student_reply="I can't do this.")
        yield ERROR
        yield event({'type': 'agent_terminal', 'data': {'failed': True}})
        yield DONE

    chunks = [c async for c in parent([])]
    assert calls == ['parent']
    assert chunks[-1] == ERROR
    assert DONE not in chunks
    assert current_journal() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('child_state', ['normal', 'error', 'cancel'])
async def test_real_agent_teacher_boundary_and_provider_count(monkeypatch, tmp_path, child_state):
    import src.agent_loop as al
    from tests.test_agent_runtime_context import _patch_fake_skills
    _patch_fake_skills(monkeypatch)
    monkeypatch.setattr('src.tool_index.get_tool_index', lambda: None)
    monkeypatch.setattr(al, '_agent_route_tool_mode', lambda *args, **kwargs: (True, False, False))
    monkeypatch.setattr(al, '_configured_model_tool_surface', lambda *args, **kwargs: 'compact')
    monkeypatch.setattr('src.model_context.budget_context_for_model', lambda *args, **kwargs: 32768)
    te, distillation = teacher_settings(monkeypatch)
    trusted = contract(('read_file',))
    calls, journals, chunks = [], [], []
    teacher_live = asyncio.Event()
    closed = []

    async def provider(candidates, messages, **kwargs):
        calls.append(1)
        journals.append(current_journal())
        assert active_turn_contract() is trusted
        names = {s.get('function', {}).get('name') for s in kwargs.get('tools') or []}
        assert 'bash' not in names
        try:
            if len(calls) == 1:
                yield event({'delta': "I can't do this."})
            else:
                assert any('teacher_takeover' in c for c in chunks)
                assert any(p.get('type') == 'metrics' and not p.get('teacher') for p in payloads(chunks))
                if child_state == 'cancel':
                    teacher_live.set()
                    await asyncio.Event().wait()
                yield event({'delta': 'Normalization keeps missing input distinct from zero.'})
                if child_state == 'error':
                    yield ERROR
                    return
            yield DONE
        finally:
            closed.append(current_journal())

    monkeypatch.setattr(al, 'stream_llm_with_fallback', provider)

    async def collect():
        async for chunk in al.stream_agent_loop(
            'http://student.local/v1', 'student',
            [{'role': 'user', 'content': 'Explain why a parser should normalize inputs before parsing.'}],
            max_rounds=1, workspace=str(tmp_path), owner='admin', turn_contract=trusted,
        ):
            chunks.append(chunk)

    if child_state == 'cancel':
        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(teacher_live.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    else:
        await collect()
    assert len(calls) == 2
    assert journals[0] is not journals[1]
    assert journals[1].parent_run_id == journals[0].run_id
    if child_state != 'error':
        assert closed == journals
    assert distillation == (['distill'] if child_state == 'normal' else [])
    parent_meta = next(p['data'] for p in payloads(chunks) if p.get('type') == 'metrics' and not p.get('teacher'))
    assert parent_meta['run_id'] == journals[0].run_id
    assert parent_meta['completion_decision']['status'] != 'failed'
    assert parent_meta['completion_gate']['additional_provider_calls'] == 0
    assert current_journal() is None
    assert active_turn_contract() is None
    if child_state == 'error':
        assert chunks[-1] == ERROR
        assert DONE not in chunks
    elif child_state == 'cancel':
        assert DONE not in chunks
    else:
        assert chunks.count(DONE) == 1
        assert chunks[-1] == DONE


@pytest.mark.asyncio
async def test_real_non_teacher_path_still_uses_one_provider_call(monkeypatch):
    import src.agent_loop as al
    from tests.test_agent_runtime_context import _patch_fake_skills
    _patch_fake_skills(monkeypatch)
    monkeypatch.setattr('src.tool_index.get_tool_index', lambda: None)
    calls = []

    async def provider(*args, **kwargs):
        calls.append(1)
        yield event({'delta': 'Normalize missing input before parsing.'})
        yield DONE

    monkeypatch.setattr(al, 'stream_llm_with_fallback', provider)
    chunks = [c async for c in al.stream_agent_loop(
        'https://api.openai.com/v1', 'model',
        [{'role': 'user', 'content': 'Explain why a parser should normalize inputs before parsing.'}],
        max_rounds=1,
    )]
    assert calls == [1]
    assert chunks.count(DONE) == 1
    assert current_journal() is None


@pytest.mark.asyncio
async def test_actual_teacher_hook_observes_closed_parent_gate(monkeypatch, tmp_path):
    import src.agent_loop as al
    import src.teacher_escalation as te
    from tests.test_agent_runtime_context import _patch_fake_skills
    _patch_fake_skills(monkeypatch)
    monkeypatch.setattr('src.tool_index.get_tool_index', lambda: None)
    monkeypatch.setattr('src.model_context.budget_context_for_model', lambda *args, **kwargs: 32768)
    chunks, calls, seen, hook_context, gate_closed = [], [], [], [], []
    trusted = contract(('read_file',))

    async def provider(*args, **kwargs):
        calls.append(1)
        yield event({'delta': "I can't do this."})
        yield DONE

    async def takeover(**kwargs):
        seen.append(kwargs)
        hook_context.append(current_journal())
        gate_closed.append(any(p.get('type') == 'completion_decision' for p in payloads(chunks))
                           and any(p.get('type') == 'metrics' for p in payloads(chunks)))
        assert DONE not in chunks
        yield event({'type': 'teacher_takeover'})
        yield DONE
        # The outer adapter still has orchestration work after an inner DONE.
        yield event({'type': 'skill_save_failed', 'reason': 'test finalization'})

    monkeypatch.setattr(al, 'stream_llm_with_fallback', provider)
    monkeypatch.setattr(te, 'run_teacher_inline', takeover)
    async for chunk in al.stream_agent_loop(
        'https://api.openai.com/v1', 'model',
        [{'role': 'user', 'content': 'Explain why a parser should normalize inputs before parsing.'}],
        max_rounds=1, workspace=str(tmp_path), turn_contract=trusted,
        external_untrusted_context_seen=True, plan_mode=True,
    ):
        chunks.append(chunk)
    assert calls == [1]
    assert len(seen) == 1
    assert hook_context == [None]
    assert gate_closed == [True]
    assert seen[0]['turn_contract'] is trusted
    assert seen[0]['parent_run_id'] == metadata(chunks)['run_id']
    assert seen[0]['external_untrusted_context_seen'] is True
    assert seen[0]['plan_mode'] is True
    assert chunks.count(DONE) == 1
    assert chunks[-1] == DONE
    assert payloads(chunks)[-1]['type'] == 'skill_save_failed'
    assert current_journal() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['awaiting_user', 'exhausted', 'error'])
async def test_actual_teacher_child_control_does_not_rewrite_parent_result(monkeypatch, tmp_path, state):
    import src.agent_loop as al
    import src.teacher_escalation as te
    from tests.test_agent_runtime_context import _patch_fake_skills
    _patch_fake_skills(monkeypatch)
    monkeypatch.setattr('src.tool_index.get_tool_index', lambda: None)
    monkeypatch.setattr('src.model_context.budget_context_for_model', lambda *args, **kwargs: 32768)

    async def provider(*args, **kwargs):
        yield event({'delta': 'The parent explains the parser.'})
        yield DONE

    @with_completion_gate
    async def child(messages, workspace=None, _parent_run_id=None):
        await tool(ToolBlock('bash', 'python -m unittest'))
        if state != 'error':
            yield event({'type': 'completion_decision', 'data': {'status': state}})
        yield event({'delta': 'The child has its own result.'})
        if state == 'error':
            yield ERROR
        yield event({'type': 'metrics', 'data': {'child_metric': 99}})
        yield DONE

    async def takeover(**kwargs):
        yield event({'type': 'teacher_takeover'})
        async with aclosing(child([], workspace=kwargs['workspace'],
                                  _parent_run_id=kwargs.get('parent_run_id'))) as stream:
            async for chunk in stream:
                if chunk.startswith('data: ') and chunk != DONE:
                    payload = json.loads(chunk[6:])
                    payload['teacher'] = True
                    chunk = event(payload)
                yield chunk

    monkeypatch.setattr(al, 'stream_llm_with_fallback', provider)
    monkeypatch.setattr(te, 'run_teacher_inline', takeover)
    chunks = [c async for c in al.stream_agent_loop(
        'https://api.openai.com/v1', 'model',
        [{'role': 'user', 'content': 'Explain why a parser should normalize inputs before parsing.'}],
        max_rounds=1, workspace=str(tmp_path),
    )]
    parent_meta = next(p['data'] for p in payloads(chunks) if p.get('type') == 'metrics' and not p.get('teacher'))
    child_meta = next(p['data'] for p in payloads(chunks) if p.get('type') == 'metrics' and p.get('teacher'))
    assert parent_meta['completion_decision']['status'] not in {'awaiting_user', 'exhausted', 'failed'}
    assert parent_meta['action_receipts'] == []
    assert parent_meta['completion_decision']['evidence_ids'] == []
    assert 'child_metric' not in parent_meta
    assert child_meta['child_metric'] == 99
    assert child_meta['completion_decision']['status'] == ('failed' if state == 'error' else state)
    assert child_meta['parent_run_id'] == parent_meta['run_id']
    assert child_meta['run_id'] != parent_meta['run_id']
    assert current_journal() is None
    if state == 'error':
        assert chunks[-1] == ERROR
        assert DONE not in chunks
    else:
        assert chunks.count(DONE) == 1
        assert chunks[-1] == DONE
