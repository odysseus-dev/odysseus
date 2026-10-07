"""Headless consumers present the completion gate's answer and close its stream."""
import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest

from src.agent_runtime.completion import with_completion_gate
from src.agent_runtime.journal import current_journal
from src.teacher_escalation import with_teacher_takeover

CLAIM = 'I created report.md and all tests passed.'


def _event(payload):
    return 'data: ' + json.dumps(payload) + '\n\n'


def _task():
    return SimpleNamespace(
        crew_member_id=None, endpoint_url='http://ep/v1', model='m',
        session_id='s', owner='admin', prompt='create report.md and run the tests',
        name='job', max_steps=5, character_id=None,
    )


def _gated_loop(released):
    """Real gate and takeover adapters around a loop that over-claims."""
    @with_teacher_takeover
    @with_completion_gate
    async def stream_agent_loop(*args, messages=None, client_runtime_context=None, **kwargs):
        yield _event({'delta': 'Inspected the layout. ' + CLAIM})
        yield _event({'type': 'metrics', 'data': {}})
        yield 'data: [DONE]\n\n'

    async def recording(*args, **kwargs):
        async for chunk in stream_agent_loop(*args, **kwargs):
            if chunk.startswith('data: {') and '"final_response"' in chunk:
                released.append(json.loads(chunk[6:])['content'])
            yield chunk

    return recording


async def test_scheduler_result_is_the_gated_replacement_without_grace_call(monkeypatch):
    from src.task_scheduler import TaskScheduler

    released = []
    grace_calls = []

    async def grace(*args, **kwargs):
        grace_calls.append(kwargs)
        return 'ungated summary: all tests passed'

    monkeypatch.setattr('src.agent_loop.stream_agent_loop', _gated_loop(released))
    monkeypatch.setattr('src.task_endpoint.resolve_task_candidates', lambda **kwargs: [])
    monkeypatch.setattr('src.task_endpoint.task_llm_call_async', grace)

    result = await TaskScheduler(session_manager=None)._run_agent_loop(
        'http://ep/v1', 'model', _task(), 's')

    assert len(released) == 1
    assert result == released[0].strip()
    assert 'Inspected the layout.' in result
    assert 'tests passed' not in result
    assert grace_calls == []


def test_background_followup_prose_is_the_gated_replacement(monkeypatch):
    from src import bg_monitor

    released = []
    agent_loop = types.ModuleType('src.agent_loop')
    agent_loop.stream_agent_loop = _gated_loop(released)
    monkeypatch.setitem(sys.modules, 'src.agent_loop', agent_loop)
    sess = SimpleNamespace(endpoint_url='http://example.test', model='model',
                           headers=None, context_length=0, id='s1', owner='owner')

    full, _ = asyncio.run(bg_monitor._drain_agent(
        sess, [{'role': 'user', 'content': 'create report.md and run the tests'}]))

    assert len(released) == 1
    assert full == released[0]
    assert 'tests passed' not in full


@pytest.mark.parametrize('consumer', ['scheduler', 'background'])
def test_later_answer_supersedes_earlier_replacement(monkeypatch, consumer):
    async def stream_agent_loop(*args, **kwargs):
        yield _event({'type': 'final_response', 'content': 'Earlier summary.'})
        yield _event({'delta': 'Final '})
        yield _event({'delta': 'answer.'})
        yield 'data: [DONE]\n\n'

    if consumer == 'scheduler':
        from src.task_scheduler import TaskScheduler
        monkeypatch.setattr('src.agent_loop.stream_agent_loop', stream_agent_loop)
        monkeypatch.setattr('src.task_endpoint.resolve_task_candidates', lambda **kwargs: [])
        result = asyncio.run(TaskScheduler(session_manager=None)._run_agent_loop(
            'http://ep/v1', 'model', _task(), 's'))
    else:
        from src import bg_monitor
        agent_loop = types.ModuleType('src.agent_loop')
        agent_loop.stream_agent_loop = stream_agent_loop
        monkeypatch.setitem(sys.modules, 'src.agent_loop', agent_loop)
        sess = SimpleNamespace(endpoint_url='http://example.test', model='model',
                               headers=None, context_length=0, id='s1')
        result, _ = asyncio.run(bg_monitor._drain_agent(sess, []))

    assert result == 'Final answer.'


async def test_scheduler_approval_pause_closes_gated_stream_in_its_own_context(monkeypatch):
    from src.task_scheduler import TaskScheduler

    closed = []
    lineage = []

    @with_teacher_takeover
    @with_completion_gate
    async def paused_loop(*args, messages=None, client_runtime_context=None, **kwargs):
        try:
            yield _event({'type': 'tool_output', 'tool': 'bash', 'output': 'Waiting for an exact user approval.',
                          'ask_user': {'kind': 'tool_approval', 'approval_id': 'missing'}})
            yield _event({'delta': 'not reached'})
        finally:
            closed.append(current_journal() is not None)

    @with_teacher_takeover
    @with_completion_gate
    async def later_loop(*args, messages=None, client_runtime_context=None, **kwargs):
        yield _event({'delta': 'Later run.'})
        yield _event({'type': 'metrics', 'data': {}})
        yield 'data: [DONE]\n\n'

    async def later_run():
        async for chunk in later_loop(messages=[{'role': 'user', 'content': 'x'}]):
            if chunk.startswith('data: {') and '"metrics"' in chunk:
                lineage.append(json.loads(chunk[6:])['data']['parent_run_id'])

    monkeypatch.setattr('src.agent_loop.stream_agent_loop', paused_loop)
    monkeypatch.setattr('src.task_endpoint.resolve_task_candidates', lambda **kwargs: [])

    result = await TaskScheduler(session_manager=None)._run_agent_loop(
        'http://ep/v1', 'model', _task(), 's')

    assert 'paused safely' in result
    # Closed during the pause, while its own journal was still bound.
    assert closed == [True]
    assert current_journal() is None
    # Neither a chained task (which copies this context) nor a later run in
    # this task inherits the paused run's journal as its parent.
    chained = asyncio.create_task(later_run())
    await chained
    await later_run()
    assert lineage == [None, None]
