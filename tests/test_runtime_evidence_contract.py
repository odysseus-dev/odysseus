"""Observable execution, stale evidence and completion-stream trust boundaries."""
import asyncio
from contextlib import aclosing
from inspect import signature
import json
import os

import pytest
from tests.runtime_evidence_helpers import server_authorized_executor


@pytest.fixture(autouse=True)
def standalone_dispatch_authority(monkeypatch):
    from src import tool_execution
    monkeypatch.setattr(tool_execution, "execute_tool_block",
                        server_authorized_executor(tool_execution.execute_tool_block))

from src.agent_evidence import CompletionRequirements, EvidenceLedger, EvidenceKind
from src.agent_runtime.completion import completion_answer, with_completion_gate
from src.agent_runtime.identity import artifact_identity, artifact_version, is_test_command, is_validation_command
from src.agent_runtime.journal import (
    ActionJournal, bind_journal, current_journal, execute_action, mark_dispatch,
    propose_action, record_action,
)
from src.tool_types import ToolBlock


@pytest.mark.parametrize('command', [
    'python -m unittest discover -s tests -v', 'python3.12 -I -m unittest tests.test_app',
    'cd /workspace && python3 -m unittest', 'pytest -q tests/test_app.py',
    '/usr/bin/python3 -m pytest', 'PYTHONPATH=. python -m unittest', 'npm run test',
])
def test_actual_foreground_test_commands(command):
    assert is_test_command(command)


@pytest.mark.parametrize('command', [
    'echo python -m unittest', 'echo "pytest passed"', 'false && pytest',
    'pytest; true', 'pytest || true', 'pytest | cat', 'python -c "print(\'pytest\')"',
    'printf "python -m unittest"', 'pytest --help', 'pytest --collect-only',
    'python -m unittest --help', 'if false; then pytest; fi', 'echo $(pytest)',
])
def test_non_execution_or_masked_status_is_not_verifier(command):
    assert not is_test_command(command)


def test_echoed_readback_is_not_validation():
    assert not is_validation_command('echo cat answer.json')
    assert is_validation_command('cat answer.json')


def test_workspace_path_aliases_and_unrelated_basenames(tmp_path):
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'a.py').write_text('x')
    (tmp_path / 'alias.py').symlink_to(tmp_path / 'a.py')
    expected = artifact_identity('a.py', str(tmp_path))
    assert all(artifact_identity(path, str(tmp_path)) == expected for path in
               ('./a.py', '/workspace/a.py', str(tmp_path / 'a.py'), 'nested/../a.py', 'alias.py'))
    assert artifact_identity('nested/a.py', str(tmp_path)) != expected
    assert artifact_identity('../a.py', str(tmp_path)) != expected
    assert artifact_identity('/workspace-other/a.py', str(tmp_path)) != expected
    assert artifact_identity('a.py.', str(tmp_path)) != expected


def test_literal_tool_path_punctuation_is_not_prose_to_strip():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'write_file', 'command': '{"path":"app.py."}', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('app.py',)))
    assert ledger.evaluate().missing_artifacts == ('app.py',)


def test_artifact_observation_does_not_open_sensitive_or_outside_files(tmp_path, monkeypatch):
    (tmp_path / '.SSH').mkdir()
    (tmp_path / '.SSH' / 'id_rsa').write_text('sensitive fixture')
    def forbidden(*args, **kwargs):
        raise AssertionError('protected artifact must not be opened')
    monkeypatch.setattr(os, 'open', forbidden)
    assert artifact_version('.SSH/id_rsa', str(tmp_path)) == 'unobserved'
    assert artifact_version('../outside', str(tmp_path)) == 'unobserved'


def test_fifo_artifact_observation_is_nonblocking(tmp_path):
    os.mkfifo(tmp_path / 'pipe')
    assert artifact_version('pipe', str(tmp_path)) == 'unobserved'


@pytest.mark.parametrize('nested', [True, False])
def test_native_argument_shapes_are_preserved_without_mutable_aliases(nested):
    function = {'name': 'provider_tool', 'arguments': {'value': 'original'}}
    native = {'function': function} if nested else function
    journal = ActionJournal()
    action = journal.propose(ToolBlock('normalized_tool', '{}'), native_call=native)
    function['arguments']['value'] = 'changed later'
    assert action.provider_arguments == {'value': 'original'}
    assert action.provider_tool == 'provider_tool'


def test_large_artifact_hashing_is_bounded(tmp_path):
    with (tmp_path / 'large.bin').open('wb') as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert artifact_version('large.bin', str(tmp_path)) == 'unobserved'


@pytest.mark.asyncio
async def test_client_completion_declaration_cannot_grant_a_host_workspace(tmp_path):
    seen = []
    @with_completion_gate
    async def stream(messages, client_runtime_context=None):
        seen.append(current_journal().workspace)
        yield 'data: {"delta":"I cannot verify that."}\n\n'
        yield 'data: [DONE]\n\n'
    context = {'completion_requirements': {'workspace_root': str(tmp_path), 'required_artifacts': ['secret.txt']}}
    _ = [chunk async for chunk in stream([], client_runtime_context=context)]
    assert seen == ['']


def test_denied_and_never_dispatched_results_are_not_authoritative():
    for flags in ({'blocked': True}, {'execution_attempted': False}, {'approval_required': True}):
        ledger = EvidenceLedger.from_tool_events([
            {'tool': 'bash', 'command': 'python -m unittest', 'exit_code': 0, **flags}],
            CompletionRequirements(verifier_required=True, executable_verifier_available=True))
        assert not ledger.evaluate().can_complete
        assert not any(e.authoritative for e in ledger.events)


def test_readback_does_not_substitute_for_required_executable_tests():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'write_file', 'command': '{"path":"answer.json"}', 'exit_code': 0},
        {'tool': 'read_file', 'command': '/workspace/answer.json', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('answer.json',), verifier_required=True,
                              executable_verifier_available=True))
    assert not ledger.evaluate().can_complete


@pytest.mark.parametrize('claim', ['All tests passed.', 'Tests: PASS', 'unittest succeeded',
                                  'Test suite ran successfully', 'No failures.', 'Done.',
                                  'I executed the command.', 'Successfully created the file.'])
def test_no_execution_receipts_cannot_support_adversarial_success_claims(claim):
    ledger = EvidenceLedger(CompletionRequirements(verifier_required=True))
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert reason
    assert answer.startswith('The task is incomplete:')


def test_declared_execution_contract_does_not_publish_invented_test_counts():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'write_file', 'command': '{"path":"app.py"}', 'exit_code': 0},
        {'tool': 'bash', 'command': 'python -m unittest', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('app.py',)))
    answer, _ = completion_answer('All 938 tests passed, 100% coverage, everything fixed.', ledger, ledger.evaluate())
    assert '938' not in answer and '100%' not in answer and 'everything' not in answer
    assert 'executable verification passed' in answer


def test_valid_explanation_survives_receipt_summary():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'write_file', 'command': '{"path":"app.py"}', 'exit_code': 0},
        {'tool': 'bash', 'command': 'python -m unittest', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('app.py',)))
    explanation = 'Empty cells are normalized before integer conversion. This avoids ValueError for missing rows.'
    answer, reason = completion_answer(explanation + '\n\nTests passed.', ledger, ledger.evaluate())
    assert explanation in answer
    assert 'Tests passed.' in answer
    assert answer.endswith('The latest executable verification passed.')
    assert not reason


def test_unattested_statistics_removed_without_erasing_explanation():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'write_file', 'command': '{"path":"app.py"}', 'exit_code': 0},
        {'tool': 'bash', 'command': 'python -m unittest', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('app.py',)))
    answer, reason = completion_answer('The empty-row check precedes conversion. All 938 tests passed, 100% coverage.\nThis keeps missing input distinct from zero.', ledger, ledger.evaluate())
    assert 'The empty-row check precedes conversion.' in answer
    assert 'This keeps missing input distinct from zero.' in answer
    assert '938' not in answer and '100%' not in answer
    assert reason


@pytest.mark.asyncio
@pytest.mark.parametrize('thinking', [True, 'Checking the result'])
async def test_mixed_thinking_delta_cannot_publish_success_before_gate(thinking):
    @with_completion_gate
    async def stream(messages):
        yield 'data: ' + json.dumps({'delta': 'All tests passed.', 'thinking': thinking}) + '\n\n'
        yield 'data: {"type":"tool_start","tool":"bash"}\n\n'
        yield 'data: {"type":"metrics","data":{"thinking":"All tests passed."}}\n\n'
        yield 'data: [DONE]\n\n'
    events = decode([chunk async for chunk in stream([{'role': 'user', 'content': 'Run the tests.'}])])
    assert events[0] == {'type': 'tool_start', 'tool': 'bash'}
    assert events[1]['type'] == 'completion_decision'
    assert not events[1]['data']['can_complete']
    assert 'All tests passed.' not in json.dumps(events)
    assert any(e.get('type') == 'final_response' and e['content'].startswith('The task is incomplete:') for e in events)


@pytest.mark.asyncio
async def test_mixed_reasoning_and_answer_preserve_saved_response_ownership():
    from routes.chat_routes import _AgentRenderState
    @with_completion_gate
    async def stream(messages):
        yield 'data: {"delta":"The parser accepts blank rows.","thinking":"Considering the input format."}\n\n'
        yield 'data: [DONE]\n\n'
    events = decode([chunk async for chunk in stream([])])
    state = _AgentRenderState()
    for event in events:
        state.consume(event)
    assert state.content == 'The parser accepts blank rows.'
    assert any(e.get('thinking') is True and e['delta'] == 'Considering the input format.' for e in events)


@pytest.mark.asyncio
async def test_unverified_metadata_claim_does_not_replace_valid_answer():
    @with_completion_gate
    async def stream(messages):
        yield 'data: {"delta":"This expression adds two values."}\n\n'
        yield 'data: {"type":"metrics","data":{"thinking":"All tests passed."}}\n\n'
        yield 'data: [DONE]\n\n'
    events = decode([chunk async for chunk in stream([])])
    assert any(e.get('delta') == 'This expression adds two values.' for e in events)
    assert 'All tests passed.' not in json.dumps(events)


@record_action
async def successful_backend(block):
    mark_dispatch()
    return block.tool_type, {'exit_code': 0, 'output': 'OK'}


@pytest.mark.asyncio
async def test_corrected_answer_preserves_safe_reasoning():
    @with_completion_gate
    async def stream(messages):
        yield 'data: {"delta":"Considering blank rows.","thinking":true}\n\n'
        yield 'data: {"delta":"All tests passed."}\n\n'
        yield 'data: [DONE]\n\n'
    events = decode([chunk async for chunk in stream([])])
    assert events[0]['type'] == 'completion_decision'
    assert events[1] == {'delta': 'Considering blank rows.', 'thinking': True}
    assert events[2]['type'] == 'final_response'
    assert 'All tests passed.' not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize('declare_before_verification', [True, False])
async def test_late_artifact_obligations_cannot_reuse_unobserved_versions(tmp_path, declare_before_verification):
    (tmp_path / 'app.py').write_text('original')
    declaration = 'data: ' + json.dumps({'type': 'metrics', 'data': {
        'completion_requirements': {'required_artifacts': ['app.py']}}}) + '\n\n'
    @with_completion_gate
    async def stream(messages, workspace=None):
        if declare_before_verification:
            yield declaration
        await successful_backend(ToolBlock('write_file', '{"path":"app.py"}'))
        await successful_backend(ToolBlock('bash', 'python -m unittest'))
        (tmp_path / 'app.py').write_text('changed after verification')
        if not declare_before_verification:
            yield declaration
        yield 'data: {"delta":"Tests passed."}\n\n'
        yield 'data: [DONE]\n\n'
    events = decode([chunk async for chunk in stream([], workspace=str(tmp_path))])
    decision = next(e['data'] for e in events if e.get('type') == 'completion_decision')
    assert not decision['can_complete']
    assert decision['status'] == 'blocked'
    assert 'Tests passed.' not in json.dumps(events)


@pytest.mark.asyncio
async def test_normalization_preserves_provider_arguments_and_replay_identity():
    journal = ActionJournal(run_id='known')
    original = ToolBlock('write_file', 'original arguments')
    normalized = ToolBlock('bash', 'python -m unittest')
    with bind_journal(journal):
        action = propose_action(original, 'native-1', {'function': {'arguments': '{"original":true}'}})
        await execute_action(successful_backend, action, normalized)
    receipt = action.to_dict()
    assert receipt['proposed_arguments'] == 'original arguments'
    assert receipt['provider_arguments'] == '{"original":true}'
    assert receipt['arguments'] == normalized.content
    assert [t['stage'] for t in receipt['transitions']] == ['proposed', 'normalized', 'authorized', 'dispatched', 'outcome']
    assert receipt['execution_id'] == 'known:action:1:execution:1'
    first = EvidenceLedger.from_tool_events(journal.evidence_events())
    replay = EvidenceLedger.from_tool_events(json.loads(json.dumps(journal.evidence_events())))
    assert first.to_list() == replay.to_list()
    assert first.evaluate().status.value == 'verified'
    assert first.events[-1].verification_id


@pytest.mark.asyncio
async def test_changed_bytes_invalidate_a_passing_verifier(tmp_path):
    path = tmp_path / 'app.py'
    path.write_text('before')
    journal = ActionJournal(workspace=str(tmp_path), observed_artifacts=('app.py',))
    with bind_journal(journal):
        await successful_backend(ToolBlock('write_file', '{"path":"app.py"}'))
        await successful_backend(ToolBlock('bash', 'python -m unittest'))
    requirements = CompletionRequirements(required_artifacts=('app.py',), workspace_root=str(tmp_path))
    assert EvidenceLedger.from_tool_events(journal.evidence_events(), requirements).evaluate().can_complete
    path.write_text('changed outside recorded call')
    decision = EvidenceLedger.from_tool_events(journal.evidence_events(), requirements).evaluate()
    assert not decision.can_complete
    assert 'changed after verification' in decision.reason


def decode(chunks):
    return [json.loads(c[6:]) for c in chunks if c.strip() != 'data: [DONE]']


@pytest.mark.asyncio
async def test_gate_holds_false_claim_until_decision_without_another_round():
    invocations = []
    @with_completion_gate
    async def stream(messages, workspace=None, client_runtime_context=None):
        invocations.append(1)
        yield 'data: {"delta":"All tests "}\n\n'
        yield 'data: {"type":"tool_start","tool":"bash"}\n\n'
        yield 'data: {"delta":"passed."}\n\n'
        yield 'data: {"type":"metrics","data":{}}\n\n'
        yield 'data: [DONE]\n\n'
    events = decode([c async for c in stream([{'role': 'user', 'content': 'Run the tests'}])])
    assert invocations == [1]
    assert events[0]['type'] == 'tool_start'
    assert events[1]['type'] == 'completion_decision'
    assert not events[1]['data']['can_complete']
    assert all('All tests passed' not in str(e) for e in events)
    assert events[2]['content'].startswith('The task is incomplete:')
    assert events[3]['data']['round_texts'] == [events[2]['content']]


@pytest.mark.asyncio
async def test_gate_preserves_verified_answer_and_sse_shape():
    @with_completion_gate
    async def stream(messages):
        await successful_backend(ToolBlock('bash', 'python -m unittest'))
        yield 'data: {"delta":"Tests passed."}\n\n'
        yield 'data: [DONE]\n\n'
    chunks = [c async for c in stream([])]
    events = decode(chunks)
    assert events[0]['data']['status'] == 'verified'
    assert events[1] == {'delta': 'Tests passed.'}
    assert chunks[-1] == 'data: [DONE]\n\n'
    assert str(signature(stream)) == '(messages)'


@pytest.mark.asyncio
async def test_cancellation_unwinds_bound_journal_without_done_or_claims():
    closed = []
    @with_completion_gate
    async def stream(messages):
        try:
            yield 'data: {"delta":"Tests passed."}\n\n'
            yield 'data: {"type":"tool_start","tool":"bash"}\n\n'
            await asyncio.Event().wait()
        finally:
            closed.append(current_journal() is not None)
    async with aclosing(stream([])) as output:
        assert json.loads((await anext(output))[6:])['type'] == 'tool_start'
    assert closed == [True]
    assert current_journal() is None


@pytest.mark.asyncio
async def test_real_unittest_dispatch_and_policy_denial_have_distinct_receipts(tmp_path, monkeypatch):
    from src.tool_execution import execute_tool_block, NO_TOOL_SECURITY_CONTEXT
    monkeypatch.setattr('src.tool_execution.owner_is_admin_or_single_user', lambda owner: True)
    (tmp_path / 'test_sample.py').write_text('import unittest\nclass TestSample(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n')
    journal = ActionJournal()
    with bind_journal(journal):
        _, denied = await execute_tool_block(ToolBlock('bash', 'python3 -m unittest'),
            workspace=str(tmp_path), disabled_tools={'bash'}, security_context=NO_TOOL_SECURITY_CONTEXT)
        _, result = await execute_tool_block(ToolBlock('bash', 'python3 -m unittest -v'),
            workspace=str(tmp_path), security_context=NO_TOOL_SECURITY_CONTEXT)
    assert denied['exit_code'] != 0
    assert journal.actions[0].execution_id is None
    assert not journal.actions[0].operation_started
    assert result['exit_code'] == 0, result
    assert 'Ran 1 test' in result['output']
    assert journal.actions[1].execution_id
    assert journal.actions[1].operation_started
    assert EvidenceLedger.from_tool_events(journal.evidence_events()).evaluate().status.value == 'verified'


@pytest.mark.asyncio
async def test_shell_writing_same_basename_elsewhere_is_not_required_mutation(tmp_path, monkeypatch):
    from src.tool_execution import execute_tool_block, NO_TOOL_SECURITY_CONTEXT
    monkeypatch.setattr('src.tool_execution.owner_is_admin_or_single_user', lambda owner: True)
    (tmp_path / 'app.py').write_text('unchanged')
    journal = ActionJournal(workspace=str(tmp_path), observed_artifacts=('app.py',))
    with bind_journal(journal):
        _, result = await execute_tool_block(ToolBlock('bash', 'mkdir nested && printf changed > nested/app.py'),
            workspace=str(tmp_path), security_context=NO_TOOL_SECURITY_CONTEXT)
    assert result['exit_code'] == 0
    assert (tmp_path / 'nested' / 'app.py').read_text() == 'changed'
    assert journal.actions[0].artifact_changes == []
    ledger = EvidenceLedger.from_tool_events(journal.evidence_events(),
        CompletionRequirements(required_artifacts=('app.py',), workspace_root=str(tmp_path)))
    assert not ledger.evaluate().can_complete


@pytest.mark.asyncio
async def test_unknown_tool_never_creates_dispatch_identity(monkeypatch):
    from src.tool_execution import execute_tool_block, NO_TOOL_SECURITY_CONTEXT
    monkeypatch.setattr('src.tool_execution.owner_is_admin_or_single_user', lambda owner: True)
    journal = ActionJournal()
    with bind_journal(journal):
        await execute_tool_block(ToolBlock('unknown_nonexistent_tool', '{}'), security_context=NO_TOOL_SECURITY_CONTEXT)
    assert journal.actions[0].execution_id is None
    assert not journal.actions[0].outcome['authoritative']


@pytest.mark.parametrize('tool,command,claim', [
    ('read_file', 'README.md', 'I ran the tests.'),
    ('bash', 'printf observation', 'The tests passed.'),
    ('read_file', 'README.md', 'I updated config.py.'),
    ('bash', 'printf observation', 'I created the file.'),
    ('write_file', '{"path":"other.py"}', 'I updated config.py.'),
    ('write_file', '{"path":"nested/config.py"}', 'I updated config.py.'),
    ('bash', 'python -m unittest', 'I ran pytest and the tests passed.'),
    ('bash', 'pytest tests/test_other.py', 'I ran pytest tests/test_config.py.'),
    ('bash', 'pytest', 'I updated config.py and the tests passed.'),
    ('write_file', '{"path":"config.py"}', 'I updated config.py and ran pytest.'),
    ('bash', 'python -m unittest pytest', 'I ran pytest.'),
    ('write_file', '{"path":"config.py"}', 'I updated "settings.py".'),
    ('bash', 'pytest', 'Created config.py and ran pytest.'),
])
def test_slice2_unrelated_receipt_cannot_support_claim(tool, command, claim):
    ledger = EvidenceLedger.from_tool_events([
        {'tool': tool, 'command': command, 'exit_code': 0},
    ])
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert reason
    assert claim not in answer


@pytest.mark.parametrize('claim', ['I ran pytest.', 'The tests passed.', 'Tests: PASS'])
def test_slice2_matching_verifier_supports_test_claim(claim):
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'bash', 'command': 'python3 -m pytest -q', 'exit_code': 0},
    ])
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert claim in answer
    assert not reason


@pytest.mark.parametrize('claim', ['I updated config.py.', 'I updated `./config.py` successfully.',
                                  'I updated "config.py".'])
def test_slice2_matching_mutation_supports_artifact_claim(claim):
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'edit_file', 'command': '{"path":"config.py"}', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('config.py',)))
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert claim in answer
    assert not reason


def test_slice2_one_matching_path_does_not_support_multiple_artifact_claims():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'edit_file', 'command': '{"path":"config.py"}', 'exit_code': 0},
    ])
    claim = 'I updated config.py and settings.py.'
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert reason
    assert claim not in answer


def test_slice2_verifier_before_mutation_cannot_support_current_test_success():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'bash', 'command': 'pytest', 'exit_code': 0},
        {'tool': 'edit_file', 'command': '{"path":"config.py"}', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('config.py',)))
    answer, reason = completion_answer('The tests passed.', ledger, ledger.evaluate())
    assert reason
    assert 'The tests passed.' not in answer


def test_slice2_matching_artifact_and_verifier_support_combined_claim():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'edit_file', 'command': '{"path":"config.py"}', 'exit_code': 0},
        {'tool': 'bash', 'command': 'pytest tests/test_config.py', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('config.py',)))
    claim = 'I updated config.py and ran pytest tests/test_config.py.'
    answer, reason = completion_answer(claim, ledger, ledger.evaluate())
    assert claim in answer
    assert not reason


@pytest.mark.parametrize('structured', [False, True])
def test_native_patch_transport_retains_mutation_evidence(structured):
    patch = '*** Begin Patch\n*** Update File: config.py\n@@\n-old\n+new\n*** End Patch'
    command = json.dumps({'patch': patch}) if structured else patch
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'apply_patch', 'command': command, 'exit_code': 0},
        {'tool': 'bash', 'command': 'pytest tests/test_config.py', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('config.py',), verifier_required=True))
    assert ledger.evaluate().status.value == 'verified'
    answer, reason = completion_answer('I updated config.py and ran pytest tests/test_config.py.',
                                      ledger, ledger.evaluate())
    assert not reason
    assert 'I updated config.py' in answer


@pytest.mark.parametrize('verifier', ['passing', 'absent', 'before_edit', 'failed'])
def test_pre_edit_inspection_requires_current_passing_executable_verification(verifier):
    events = [{'tool': 'read_file', 'command': 'config.py', 'exit_code': 0}]
    if verifier == 'before_edit':
        events.append({'tool': 'bash', 'command': 'pytest', 'exit_code': 0})
    events.append({'tool': 'edit_file', 'command': '{"path":"config.py"}', 'exit_code': 0})
    if verifier in {'passing', 'failed'}:
        events.append({'tool': 'bash', 'command': 'pytest', 'exit_code': 0 if verifier == 'passing' else 1})
    ledger = EvidenceLedger.from_tool_events(events, CompletionRequirements(
        required_artifacts=('config.py',), verifier_required=True, executable_verifier_available=True))
    decision = ledger.evaluate()
    assert decision.can_complete is (verifier == 'passing')
    assert (decision.status.value == 'verified') is (verifier == 'passing')


def test_failed_post_edit_inspection_is_not_hidden_by_passing_tests():
    ledger = EvidenceLedger.from_tool_events([
        {'tool': 'edit_file', 'command': '{"path":"config.py"}', 'exit_code': 0},
        {'tool': 'read_file', 'command': 'config.py', 'exit_code': 1},
        {'tool': 'bash', 'command': 'pytest', 'exit_code': 0},
    ], CompletionRequirements(required_artifacts=('config.py',), verifier_required=True))
    assert not ledger.evaluate().can_complete
    assert ledger.evaluate().status.value == 'failed'


@pytest.mark.parametrize('command, expected', [
    ('"$runner" -m pytest -q tests/test_config.py', True),
    ('"$runner" -m unittest', True),
    ('"$runner" -m pytest --collect-only', False),
    ('"$runner" -m pytest || true', False),
    ('"$runner" -m pytest; echo passed', False),
    ('"$runner" -m pytest $FLAGS', False),
    ('echo pytest passed', False),
])
def test_exact_tui_interpreter_selection_preserves_foreground_verifier_status(command, expected):
    from src.agent_loop import _tui_python_runner_setup
    assert is_test_command(_tui_python_runner_setup() + command) is expected


def test_tui_discovery_fallback_cannot_attest_that_tests_executed():
    from src.agent_loop import _tui_local_test_runner_command
    assert not is_test_command(_tui_local_test_runner_command())


@pytest.mark.parametrize('command, expected', [('pytest -q', True), ('pytest || true', False)])
def test_host_shell_native_arguments_use_the_same_verifier_classification(command, expected):
    from src.agent_evidence import command_is_test, command_is_validation
    encoded = json.dumps({'command': command, 'timeout': 120})
    assert command_is_test(encoded) is expected
    assert command_is_validation(encoded) is expected
