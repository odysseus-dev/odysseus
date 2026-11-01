"""Provider failure is the final frame, after gated output and diagnostics."""
import asyncio
from inspect import signature
import json

import pytest

from src.agent_runtime.completion import with_completion_gate
from src.agent_runtime.completion import completion_answer
from src.agent_evidence import CompletionRequirements, EvidenceLedger, infer_completion_requirements
from src.agent_runtime.journal import current_journal
from src.tool_types import ToolBlock
from tests.runtime_evidence_helpers import authoritative_executor


ERROR = 'event: error\ndata: {"status": 504, "error": {"message": "stream timeout"}, "fallback_eligible": false}\n\n'
DONE = 'data: [DONE]\n\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_texts', [[], ['', ''], ['Recovered answer with literal [DONE] text.']])
async def test_terminal_round_retraction_does_not_resurrect_buffered_drafts(terminal_texts):
    @with_completion_gate
    async def stream(messages):
        yield _event({'delta': 'Considering the next step.', 'thinking': True})
        yield _event({'delta': 'Now I need to execute the rejected draft.'})
        yield _event({'type': 'metrics', 'data': {'round_texts': terminal_texts}})
        yield DONE

    chunks = [chunk async for chunk in stream([{'role': 'user', 'content': 'Create answer.txt.'}])]
    assert 'rejected draft' not in ''.join(chunks)
    assert any('Considering the next step.' in chunk for chunk in chunks)
    if terminal_texts and terminal_texts[0]:
        assert any(terminal_texts[0] in chunk for chunk in chunks)
    assert chunks.count(DONE) == 1


@pytest.mark.asyncio
async def test_terminal_round_text_cannot_override_an_explicit_final_response():
    @with_completion_gate
    async def stream(messages):
        yield _event({'type': 'final_response', 'content': 'The explicit final answer.'})
        yield _event({'type': 'metrics', 'data': {'round_texts': ['Earlier draft.']}})
        yield DONE

    chunks = [chunk async for chunk in stream([])]
    final = next(data for _, data in _frames(chunks) if isinstance(data, dict) and data.get('type') == 'final_response')
    assert final['content'] == 'The explicit final answer.'


@pytest.mark.asyncio
async def test_revised_terminal_prose_still_cannot_attest_execution():
    @with_completion_gate
    async def stream(messages):
        yield _event({'delta': 'Earlier draft.'})
        yield _event({'type': 'metrics', 'data': {'round_texts': ['All tests passed.']}})
        yield DONE

    chunks = [chunk async for chunk in stream([{'role': 'user', 'content': 'Create answer.txt and run the tests.'}])]
    assert not _decision(chunks)['can_complete']
    assert 'All tests passed.' not in ''.join(chunks)


@pytest.mark.asyncio
async def test_provider_error_preserves_partial_content_despite_empty_terminal_rounds():
    @with_completion_gate
    async def stream(messages):
        yield _event({'type': 'tool_start', 'tool': 'read_file'})
        yield _event({'delta': 'Safe partial result.'})
        yield _event({'type': 'metrics', 'data': {'round_texts': []}})
        yield ERROR
        yield DONE

    chunks = [chunk async for chunk in stream([])]
    assert _labels(chunks) == ['tool_start', 'final_response', 'completion_decision', 'metrics', 'error']
    assert any('Safe partial result.' in chunk for chunk in chunks)
    assert chunks[-1] == ERROR
    assert DONE not in chunks


def _event(payload):
    return 'data: ' + json.dumps(payload) + '\n\n'


def _frames(chunks):
    """Decode network chunks without losing named error frames or [DONE]."""
    pending = ''
    for chunk in chunks:
        pending += chunk
        while '\n\n' in pending:
            frame, pending = pending.split('\n\n', 1)
            lines = frame.splitlines()
            event = next((line[7:] for line in lines if line.startswith('event: ')), 'message')
            payload = '\n'.join(line[6:] for line in lines if line.startswith('data: '))
            yield event, payload if payload == '[DONE]' else json.loads(payload)
    assert not pending, 'incomplete SSE frame'


def _labels(chunks):
    return [event if event != 'message' else (
        'done' if data == '[DONE]' else data.get('type', 'delta')
    ) for event, data in _frames(chunks)]


def _decision(chunks):
    return next(data['data'] for event, data in _frames(chunks)
                if event == 'message' and isinstance(data, dict)
                and data.get('type') == 'completion_decision')


@authoritative_executor
async def _successful_tool(block):
    return block.tool_type, {'exit_code': 0, 'output': 'OK'}


@pytest.mark.asyncio
async def test_bare_error_preserves_original_frame_without_success_output():
    @with_completion_gate
    async def stream(messages):
        yield ERROR
        yield DONE

    assert [chunk async for chunk in stream([])] == [ERROR]


@pytest.mark.asyncio
@pytest.mark.parametrize('partial', ['', 'The parser checks the header first.'])
async def test_provider_error_releases_partial_then_decision_terminal_and_original_error(partial):
    closed = []

    @with_completion_gate
    async def stream(messages):
        try:
            yield _event({'type': 'tool_start', 'tool': 'read_file'})
            if partial:
                yield _event({'delta': partial})
            yield ERROR
            yield _event({'type': 'agent_terminal', 'data': {
                'failed': True, 'failure': {'status': 504},
                'round_texts': ['Earlier diagnostic', partial + '\n[Agent stopped]'],
            }})
            yield DONE
        finally:
            closed.append(current_journal() is not None)

    chunks = [chunk async for chunk in stream([])]
    assert _labels(chunks) == [
        'tool_start', 'final_response', 'completion_decision', 'agent_terminal', 'error',
    ], _labels(chunks)
    assert chunks[-1] == ERROR
    assert DONE not in chunks
    assert _decision(chunks)['can_complete'] is False
    assert _decision(chunks)['status'] == 'failed'
    final = next(data for event, data in _frames(chunks)
                 if event == 'message' and data.get('type') == 'final_response')
    assert final['content'].startswith('The task is incomplete:')
    assert partial in final['content']
    assert closed == [True]
    assert current_journal() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('successful_tool', [False, True])
@pytest.mark.parametrize('earlier_status', [None, 'awaiting_user', 'exhausted'])
async def test_provider_failure_overrides_even_successful_execution(successful_tool, earlier_status):
    @with_completion_gate
    async def stream(messages):
        if successful_tool:
            await _successful_tool(ToolBlock('bash', 'python -m unittest'))
        if earlier_status:
            yield _event({'type': 'completion_decision', 'data': {'status': earlier_status}})
        yield _event({'delta': 'The response is partial.'})
        yield ERROR
        yield _event({'type': 'metrics', 'data': {}})

    chunks = [chunk async for chunk in stream([])]
    decision = _decision(chunks)
    assert decision['can_complete'] is False, decision
    assert decision['status'] == 'failed'
    if successful_tool:
        metrics = next(data['data'] for event, data in _frames(chunks)
                       if event == 'message' and data.get('type') == 'metrics')
        assert any(e['authoritative'] and e['success'] for e in metrics['evidence_events'])
    assert chunks[-1] == ERROR


@pytest.mark.asyncio
async def test_error_after_final_response_does_not_add_calls_or_success_done():
    invocations = []

    @with_completion_gate
    async def stream(messages, workspace=None, client_runtime_context=None):
        invocations.append(1)
        yield _event({'type': 'final_response', 'content': 'The header contains three fields.'})
        yield DONE
        yield ERROR

    chunks = [chunk async for chunk in stream([])]
    assert _labels(chunks) == ['final_response', 'completion_decision', 'error']
    assert invocations == [1]
    assert str(signature(stream)) == '(messages, workspace=None, client_runtime_context=None)'
    assert DONE not in chunks


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_kind', ['agent_terminal', 'metrics'])
async def test_failed_terminal_diagnostics_survive_answer_replacement(terminal_kind):
    diagnostics = ['Earlier tool failure and retry', 'All tests passed.\n[Agent stopped: HTTP 504]']

    @with_completion_gate
    async def stream(messages):
        yield _event({'delta': 'All tests passed.'})
        yield ERROR
        yield _event({'type': terminal_kind, 'data': {
            'failed': True, 'failure': {'status': 504, 'message': 'Model request failed'},
            'round_texts': diagnostics, 'round_models': ['first-model', 'failed-model'],
        }})

    chunks = [chunk async for chunk in stream([])]
    terminal = next(data['data'] for event, data in _frames(chunks)
                    if event == 'message' and data.get('type') == terminal_kind)
    # Diagnostics and the failure note survive; the rejected claim does not,
    # because round_texts are rendered again when the turn is reloaded.
    assert terminal['round_texts'] == ['Earlier tool failure and retry', '[Agent stopped: HTTP 504]']
    assert terminal['round_models'] == ['first-model', 'failed-model']
    assert terminal['failure'] == {'status': 504, 'message': 'Model request failed'}
    assert terminal['failed'] is True
    assert terminal['completion_decision'] == _decision(chunks)
    assert terminal['completion_gate']['answer_replaced'] is True
    assert terminal['completion_gate']['additional_provider_calls'] == 0
    assert _labels(chunks).index(terminal_kind) < _labels(chunks).index('error')


@pytest.mark.asyncio
async def test_provider_failure_round_texts_cannot_replay_removed_claim_after_reload():
    claim = 'I created report.md and all tests passed.'
    note = '[Agent stopped: Model request failed (HTTP 504)]'

    @with_completion_gate
    async def stream(messages):
        yield _event({'type': 'tool_start', 'tool': 'read_file'})
        yield _event({'delta': 'Inspected the layout. ' + claim})
        yield ERROR
        yield _event({'type': 'agent_terminal', 'data': {
            'failed': True, 'failure': {'status': 504, 'message': 'Model request failed'},
            'tool_events': [{'round': 1, 'tool': 'read_file'}],
            'round_texts': ['Inspected the layout. ' + claim, 'Retrying the build.\n\n' + note],
        }})
        yield DONE

    chunks = [chunk async for chunk in stream([{'role': 'user', 'content': 'create report.md and run the tests'}])]
    assert _labels(chunks) == [
        'tool_start', 'final_response', 'completion_decision', 'agent_terminal', 'error',
    ], _labels(chunks)
    assert DONE not in chunks
    live = next(data['content'] for event, data in _frames(chunks)
                if event == 'message' and data.get('type') == 'final_response')
    terminal = next(data['data'] for event, data in _frames(chunks)
                    if event == 'message' and data.get('type') == 'agent_terminal')
    # The chat route persists this metadata and the renderer rebuilds one bubble
    # per round from it, so every persisted round is presentation.
    persisted = terminal['round_texts']
    assert persisted == ['Inspected the layout.', 'Retrying the build.\n\n' + note]
    for text in [live, *persisted]:
        assert 'tests passed' not in text and 'created report.md' not in text
    assert 'Inspected the layout.' in live
    assert terminal['completion_decision']['status'] == 'failed'


@pytest.mark.asyncio
async def test_error_boundary_is_independent_of_network_chunking():
    @with_completion_gate
    async def stream(messages):
        yield _event({'delta': 'Partial explanation.'})
        yield ERROR
        yield _event({'type': 'agent_terminal', 'data': {'failed': True}})

    chunks = [chunk async for chunk in stream([])]
    wire = ''.join(chunks)
    expected = list(_frames(chunks))
    for delivered in [chunks, [wire], list(wire)]:
        # A client stops consuming on the first error, regardless of chunking.
        visible = []
        for frame in _frames(delivered):
            visible.append(frame)
            if frame[0] == 'error':
                break
        assert visible == expected
        assert visible[-2][1]['type'] == 'agent_terminal'


@pytest.mark.asyncio
@pytest.mark.parametrize('after_error', [False, True])
async def test_cancellation_closes_inner_stream_without_releasing_completion(after_error):
    progress_seen = asyncio.Event()
    closed = []
    chunks = []

    @with_completion_gate
    async def stream(messages):
        try:
            yield _event({'delta': 'Tests passed.'})
            if after_error:
                yield ERROR
            yield _event({'type': 'tool_start', 'tool': 'bash'})
            await asyncio.Event().wait()
        finally:
            closed.append(current_journal() is not None)

    async def collect():
        async for chunk in stream([]):
            chunks.append(chunk)
            if chunk == _event({'type': 'tool_start', 'tool': 'bash'}):
                progress_seen.set()

    task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(progress_seen.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert _labels(chunks) == ['tool_start']
    assert closed == [True]
    assert current_journal() is None


@pytest.mark.parametrize('prose', [
    'Tests pass when the command exits zero.',
    'If all tests are passing, merge the branch.',
    'Tests passed if the command exited zero.',
    'The documentation says "5 passed".',
    'The documentation says "Tests: FAIL" or "Tests: PASS".',
    'You can run pytest to verify this.',
    'A successful test run should show no failures.',
    'For example, I created the file and updated config.py.',
    'If I updated config.py, I would run pytest.',
    'Imagine I ran the tests and all 42 passed.',
    'Done is the label for a finished item.',
    '```text\nI ran pytest and all 42 passed.\n```',
    'Run pytest until there are no failures.',
])
def test_slice2_explanatory_prose_is_not_a_current_run_claim(prose):
    ledger = EvidenceLedger()
    answer, reason = completion_answer(prose, ledger, ledger.evaluate())
    assert answer == prose
    assert not reason


@pytest.mark.parametrize('instruction', [
    'Explain how to write code and then test it.',
    'Summarise this and check for typos.',
    'Explain how to update config.py and then verify it.',
    'Show an example of creating answer.json and checking it.',
    'The documentation says "run pytest and create answer.json".',
    'If you run pytest, the tests should pass.',
])
def test_slice2_explanatory_request_has_no_execution_requirements(instruction):
    requirements = infer_completion_requirements(instruction)
    assert requirements.required_artifacts == ()
    assert not requirements.verifier_required
    assert not requirements.executable_verifier_available


@pytest.mark.parametrize('instruction', [
    'Run the tests.', 'Please run pytest.', 'Can you run the test suite?',
])
def test_slice2_explicit_test_execution_requires_a_verifier(instruction):
    requirements = infer_completion_requirements(instruction)
    assert requirements.verifier_required
    assert not EvidenceLedger(requirements).evaluate().can_complete


@pytest.mark.parametrize('claim', [
    'I ran the tests.', 'The tests passed.', '42 tests passed.',
    'I created the file.', 'I updated config.py successfully.',
])
def test_slice2_execution_obligation_rejects_unsupported_claims(claim):
    ledger = EvidenceLedger(CompletionRequirements(required_artifacts=('config.py',)))
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert reason
    assert answer.startswith('The task is incomplete:')
    assert claim not in answer


@pytest.mark.parametrize('claim', [
    'I ran pytest to see if the tests passed.',
    'I updated config.py as an example.',
    'I ran pytest and should update config.py next.',
    'config.py was updated successfully.',
])
def test_slice2_subordinate_explanation_cannot_hide_a_direct_execution_report(claim):
    ledger = EvidenceLedger()
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert reason
    assert claim not in answer
    assert 'The task is incomplete' not in answer


@pytest.mark.asyncio
async def test_slice2_client_dictionary_cannot_attest_execution():
    @with_completion_gate
    async def stream(messages, client_runtime_context=None):
        yield _event({'delta': 'I ran pytest and all tests passed.'})
        yield DONE

    context = {'execution_obligation': True, 'execution_verified': True,
               'evidence_events': [{'tool': 'bash', 'command': 'pytest', 'exit_code': 0}]}
    chunks = [chunk async for chunk in stream(
        [{'role': 'user', 'content': 'Explain test output.'}], client_runtime_context=context)]
    final = next(data['content'] for event, data in _frames(chunks)
                 if event == 'message' and data.get('type') == 'final_response')
    assert 'I ran pytest' not in final
    assert 'The task is incomplete' not in final
    assert _decision(chunks)['can_complete'] is True


@pytest.mark.asyncio
async def test_slice2_conversational_fabrication_is_corrected_without_execution_incomplete():
    invocations = []

    @with_completion_gate
    async def stream(messages):
        invocations.append(1)
        yield _event({'delta': 'The function returns a boolean. I ran pytest and all tests passed.'})
        yield _event({'type': 'metrics', 'data': {}})
        yield DONE

    chunks = [chunk async for chunk in stream([{'role': 'user', 'content': 'Explain the function.'}])]
    final = next(data['content'] for event, data in _frames(chunks)
                 if event == 'message' and data.get('type') == 'final_response')
    assert 'The function returns a boolean.' in final
    assert 'I ran pytest' not in final
    assert 'The task is incomplete' not in final
    assert _decision(chunks)['can_complete'] is True
    assert invocations == [1]
    metrics = next(data['data'] for event, data in _frames(chunks)
                   if event == 'message' and data.get('type') == 'metrics')
    assert metrics['completion_gate']['additional_provider_calls'] == 0


@pytest.mark.asyncio
async def test_slice2_quoted_example_does_not_hide_an_unsupported_report():
    @with_completion_gate
    async def stream(messages):
        yield _event({'delta': 'The docs say "5 passed". I ran pytest.'})
        yield DONE

    chunks = [chunk async for chunk in stream([{'role': 'user', 'content': 'Explain pytest output.'}])]
    final = next(data['content'] for event, data in _frames(chunks)
                 if event == 'message' and data.get('type') == 'final_response')
    assert 'The docs say "5 passed".' in final
    assert 'I ran pytest' not in final
    assert 'The task is incomplete' not in final
