import copy
import json
import os
from types import SimpleNamespace

import pytest

from src.clean_agent_preview import conversation
from src.tool_policy import ToolPolicy
from src.tool_routing_experiment import MODEL_CHOICE_MODE, select_experiment_inventory
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import (requested_capabilities, resolve_turn_contract,
                               resolve_full_inventory_contract, result_reference_followup)


def search_history(body_size=0):
    return [
        {'role': 'user', 'content': 'Search when the new folding phone is out'},
        {'role': 'assistant', 'content': 'The release date is in these reports.', 'metadata': {
            'tool_events': [{'tool': 'web_search', 'exit_code': 0}],
            'clean_v3_turn': [
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'search1', 'type': 'function', 'function': {
                        'name': 'web_search', 'arguments': '{"query":"folding phone"}'}}]},
                {'role': 'tool', 'tool_call_id': 'search1', 'content':
                 '```sources\n[1] Release guide\n https://example.org/release\n'
                 '[2] Details\n https://example.org/details\n```\n' + 'x' * body_size},
            ],
        }},
    ]


@pytest.mark.parametrize('prompt', ['Link more info', 'More information', 'More details about that',
    'Send the links', 'Please show me the source', 'Open that', 'Read the second result'])
@pytest.mark.parametrize('mode', ['baseline', MODEL_CHOICE_MODE])
def test_referential_web_inventory_does_not_offer_files(prompt, mode):
    history = search_history()
    capabilities = requested_capabilities(prompt, history)
    assert capabilities == {'search_browser'}
    policy = ToolPolicy(disabled_tools=frozenset({'private_browser'}))
    routed = resolve_turn_contract(capabilities=capabilities, schemas=FUNCTION_TOOL_SCHEMAS, policy=policy)
    inventory = resolve_full_inventory_contract(schemas=FUNCTION_TOOL_SCHEMAS, policy=policy)
    result = select_experiment_inventory(inventory, routed, history, mode, user_text=prompt)
    assert 'web_fetch' in result.offered
    assert not result.offered & {'read_file', 'bash', 'python', 'private_browser'}
    assert not result.required


@pytest.mark.parametrize('prompt', ['Read /workspace/notes.md', 'Search my email',
    'Save that link as a note', 'More details about my calendar',
    'Send the link by email', 'Open that and run a Python script'])
def test_new_task_is_not_a_source_only_followup(prompt):
    assert not result_reference_followup(prompt)


@pytest.mark.parametrize('body_size', [0, 30000])
def test_observed_source_references_survive_trimming(body_size):
    history = search_history(body_size)
    original = copy.deepcopy(history)
    result = conversation(SimpleNamespace(history=history), [{'role': 'user', 'content': 'Link more info'}])
    context = result[-2]
    assert context['metadata']['trusted'] is False
    assert context['metadata']['source'] == 'previous turn source references'
    assert '[Source: Release guide](https://example.org/release)' in context['content']
    assert 'https://example.org/details' in context['content']
    assert len(context['content']) < 2000
    assert result[-1]['content'] == 'Link more info'
    assert history == original
    if body_size:
        assert not any(m['role'] == 'tool' for m in result)


def test_old_sources_do_not_cross_an_intervening_topic():
    history = search_history() + [
        {'role': 'user', 'content': 'What is 2+2?'},
        {'role': 'assistant', 'content': '4'},
    ]
    result = conversation(SimpleNamespace(history=history), [{'role': 'user', 'content': 'More info'}])
    assert not any(m.get('metadata', {}).get('source') == 'previous turn source references' for m in result)


def test_assistant_prose_is_not_observed_source_evidence():
    history = [{'role': 'user', 'content': 'Search for phones'},
               {'role': 'assistant', 'content': 'https://invented.example/release'}]
    result = conversation(SimpleNamespace(history=history), [{'role': 'user', 'content': 'Link more info'}])
    assert not any(m.get('metadata', {}).get('source') == 'previous turn source references' for m in result)


def test_result_reference_uses_the_actual_domain():
    history = [{'role': 'user', 'content': 'Search my notes'},
               {'role': 'assistant', 'content': 'Found a note', 'metadata': {
                   'tool_events': [{'tool': 'manage_notes', 'exit_code': 0}]}}]
    assert requested_capabilities('More info', history) == {'notes'}


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('ODYSSEUS_AJAX_TEST_URL'), reason='Opt-in live Ajax replay')
async def test_live_ajax_links_observed_sources_without_local_files(monkeypatch):
    import src.clean_agent_preview as preview
    calls = []

    async def execute(block, *args, **kwargs):
        calls.append(block.tool_type)
        return 'fixture', {'exit_code': 0, 'output':
            '[1] Release guide\n https://example.org/release\n'
            '[2] Details\n https://example.org/details'}

    monkeypatch.setattr(preview, 'execute_tool_block', execute)
    rows = search_history(30000)
    policy = ToolPolicy()
    prompt = 'Link more info'
    routed = resolve_turn_contract(capabilities=requested_capabilities(prompt, rows),
                                   schemas=FUNCTION_TOOL_SCHEMAS, policy=policy)
    inventory = resolve_full_inventory_contract(schemas=FUNCTION_TOOL_SCHEMAS, policy=policy)
    contract = select_experiment_inventory(inventory, routed, rows, MODEL_CHOICE_MODE, user_text=prompt)
    events = [json.loads(chunk[6:]) async for chunk in preview.stream_preview(
        endpoint_url=os.environ['ODYSSEUS_AJAX_TEST_URL'], model='Ajax', headers={},
        turn_contract=contract, messages=[{'role': 'user', 'content': prompt}],
        history_session=SimpleNamespace(history=rows), session_id='source-reference-test',
        owner='test', disabled_tools=set(), tool_policy=policy, thinking_mode='off',
    ) if '[DONE]' not in chunk]
    answer = ''.join(e.get('delta', '') or e.get('content', '') for e in events)
    # The renderer supports both Markdown links and bare HTTP(S) autolinks.
    assert 'https://example.org/release' in answer, json.dumps(events, ensure_ascii=False)
    assert 'https://example.org/details' in answer, answer
    assert not set(calls) & {'read_file', 'bash', 'python'}
