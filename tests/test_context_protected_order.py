"""Trimming retains priority context in place, including native tool evidence."""
from copy import deepcopy

import pytest

from src.context_compactor import trim_for_context
from src.model_context import estimate_tokens


def document(text='DOCUMENT_MARKER ' * 25):
    return {'role': 'user', 'content': text, '_protected': True,
            'metadata': {'source': 'active editor document', 'trusted': False}}


@pytest.mark.parametrize('system', ['extra', 'large-preset', 'old-history'])
def test_document_stays_between_surviving_history_and_current_question(system):
    messages = [{'role': 'system', 'content': 'Helpful preset.'}]
    if system == 'extra':
        messages.append({'role': 'system', 'content': 'optional memory ' * 6000})
    elif system == 'large-preset':
        messages[0]['content'] = 'Helpful preset. ' * 4000
    else:
        messages.extend({'role': 'user', 'content': f'old-{i} ' + 'history ' * 100} for i in range(15))
    messages.extend([{'role': 'user', 'content': 'OLDER_QUESTION'},
                     {'role': 'assistant', 'content': 'OLDER_ANSWER'},
                     document(), {'role': 'user', 'content': 'CURRENT_QUESTION'}])
    original = deepcopy(messages)
    assert estimate_tokens(messages) > 3584
    result = trim_for_context(messages, 4096, 512)
    contents = [m.get('content') for m in result]
    assert contents.index('OLDER_QUESTION') < contents.index(document()['content']) < contents.index('CURRENT_QUESTION')
    assert result[-2] == document()
    assert messages == original
    assert estimate_tokens(result) <= 3584


def test_multiple_protected_messages_preserve_relative_positions():
    first, second = document('FIRST_DOCUMENT'), document('SECOND_EMAIL')
    messages = [{'role': 'system', 'content': 'preset'}, {'role': 'system', 'content': 'optional ' * 10000},
                {'role': 'user', 'content': 'FIRST_QUESTION'}, first,
                {'role': 'assistant', 'content': 'MIDDLE_ANSWER'}, second,
                {'role': 'user', 'content': 'LAST_QUESTION'}]
    result = trim_for_context(messages, 4096, 512)
    assert [m['content'] for m in result] == ['preset', 'FIRST_QUESTION', 'FIRST_DOCUMENT', 'MIDDLE_ANSWER', 'SECOND_EMAIL', 'LAST_QUESTION']


def test_protected_context_is_not_double_charged_or_truncated_with_tool_tail():
    protected = document('DENSE_DOCUMENT ' * 90)
    messages = [{'role': 'system', 'content': 'preset'}, protected,
                {'role': 'user', 'content': 'CURRENT_QUESTION'},
                {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'web_fetch', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'unprotected source ' * 6000}]
    result = trim_for_context(messages, 2048, 512)
    assert protected in result
    assert any(m.get('tool_call_id') == 'a' for m in result)
    assert estimate_tokens(result) <= 1536


@pytest.mark.parametrize('budget', [4096, 800])
@pytest.mark.parametrize('protected_part', ['call', 'response'])
def test_protected_tool_result_keeps_its_valid_call_batch(budget, protected_part):
    call = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]}
    response = {'role': 'tool', 'tool_call_id': 'a', 'content': 'PRIORITY_SOURCE'}
    (call if protected_part == 'call' else response)['_protected'] = True
    messages = [{'role': 'system', 'content': 'preset'}, {'role': 'system', 'content': 'optional ' * 10000},
                {'role': 'user', 'content': 'Read the file'}, call, response,
                {'role': 'user', 'content': 'CURRENT_QUESTION ' + 'dense input ' * 3000}]
    result = trim_for_context(messages, budget, 256)
    assert response in result
    index = result.index(response)
    assert result[index-1].get('tool_calls') == call['tool_calls']
    assert result[index-2]['content'] == 'Read the file'
    assert any(m.get('content', '').startswith('CURRENT_QUESTION') for m in result if isinstance(m.get('content'), str))
    assert estimate_tokens(result) <= budget-256


def test_protected_context_survives_last_ditch_without_becoming_the_direct_request():
    protected = document('DOCUMENT_MARKER ' * 100)
    messages = [{'role': 'system', 'content': 'preset'},
                {'role': 'user', 'content': 'CURRENT_QUESTION ' + '\ufffd' * 10000}, protected]
    result = trim_for_context(messages, 800, 256)
    assert protected in result
    assert any('CURRENT_QUESTION' in str(m.get('content')) for m in result if not m.get('_protected'))
    assert protected['content'] == next(m['content'] for m in result if m.get('_protected'))
    assert result[-1] == protected


def test_under_budget_keeps_input_identity_and_order():
    messages = [{'role': 'user', 'content': 'older'}, document(), {'role': 'user', 'content': 'current'}]
    assert trim_for_context(messages, 10000, 512) is messages
