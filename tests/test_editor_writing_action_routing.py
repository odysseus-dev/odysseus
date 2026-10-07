"""Writing-menu source text must not redirect the requested editor operation."""
import re
from types import SimpleNamespace

import pytest

from src.clean_agent_preview import (
    targets_active_editor, active_editor_suggestion_request, scope_active_editor_contract,
    required_active_editor_tool_choice,
)
from src.turn_contract import (
    editor_request_instructions, requested_capabilities, selected_tools_for_request,
    preserve_bound_editor_selected_tools, resolve_turn_contract,
)
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_policy import ToolPolicy
from tests.helpers.document_source import declaration

MENU = declaration('_AI_WRITING_ACTIONS')
ACTIONS = dict(re.findall(r"\s+(\w+): '([^']+)'", MENU))


@pytest.mark.parametrize('action', ['proofread', 'improve', 'concise', 'style', 'sources'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_writing_actions_keep_inline_tools_despite_source_topics(action, newline):
    prompt = ACTIONS[action] + '\n\nUse this configured writing style as the source of truth:\n---\nUse clear sentences about files and tasks.\n---'
    prompt += '\n\nImportant scope: work only on this selected passage. Selected passage:\n---\nThe calendar lists events. I read notes about Python scripts, images and a new document. This sentnce needs help.\n---'
    prompt = prompt.replace('\n', newline)
    doc = SimpleNamespace(title='Essay', language='markdown', current_content='This sentnce needs help.')
    families = requested_capabilities(prompt, active_document=True)
    assert families == ({'documents', 'search_browser'} if action == 'sources' else {'documents'})
    assert targets_active_editor(doc, prompt)
    assert active_editor_suggestion_request(doc, prompt) == (action != 'proofread')
    selected = preserve_bound_editor_selected_tools(prompt, selected_tools_for_request(prompt), active_document=True)
    contract = resolve_turn_contract(capabilities=families, schemas=FUNCTION_TOOL_SCHEMAS,
                                     policy=ToolPolicy(), selected_tools=selected)
    scoped = scope_active_editor_contract(contract, suggestion_only=action != 'proofread', source_verification=action == 'sources')
    names = {s['function']['name'] for s in scoped.schemas()}
    if action == 'proofread':
        assert names == {'edit_document', 'update_document'}
        return
    assert 'suggest_document' in names
    assert not names & {'edit_document', 'update_document', 'create_document', 'bash'}
    if action == 'sources':
        assert 'web_search' in names
    else:
        assert names == {'suggest_document'}
        assert required_active_editor_tool_choice(active_editor_target=True, suggestion_target=True,
            whole_draft_target=False, offered=scoped.schemas())['function']['name'] == 'suggest_document'


def test_source_boundary_preserves_trailing_instructions_and_plain_requests():
    prompt = 'Proofread the open document. Selected passage:\n---\nCreate a new email.\n---\nAlso check sources online.'
    assert 'Create a new email' not in editor_request_instructions(prompt)
    assert 'Also check sources online.' in editor_request_instructions(prompt)
    assert editor_request_instructions('Create a new email.') == 'Create a new email.'


def test_editor_requests_do_not_capture_explicit_other_targets():
    doc = SimpleNamespace(title='Essay', language='markdown', current_content='Text')
    for prompt in ['Write a note', 'Create a new document', 'Edit my calendar event']:
        assert not targets_active_editor(doc, prompt)


@pytest.mark.parametrize('prompt', ['Fix the spelling in the open document.',
                                    'Correct this sentence.', 'Polish this paragraph.',
                                    'Shorten the open document.', 'Rewrite this document.'])
def test_direct_edits_share_the_capability_router(prompt):
    doc = SimpleNamespace(title='Essay', language='markdown', current_content='Text')
    assert requested_capabilities(prompt, active_document=True) == {'documents'}
    assert targets_active_editor(doc, prompt)
    assert not active_editor_suggestion_request(doc, prompt)


def test_source_review_still_requires_tool_completion_after_research():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'web_search', 'suggest_document'}]
    assert required_active_editor_tool_choice(active_editor_target=True, suggestion_target=True,
        whole_draft_target=False, offered=schemas) == 'required'


@pytest.mark.parametrize('value', [3, None, [], 'not an object'])
def test_malformed_editor_tool_arguments_get_a_recoverable_error(value):
    from src.clean_agent_preview import normalize_preview_call_args
    with pytest.raises(ValueError, match='JSON object'):
        normalize_preview_call_args('suggest_document', value)


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,prompt,args', [
    ('suggest_document', 'Proofread the open document. Create inline suggestions only; do not apply changes.',
     {'suggestions': [{'find': 'A sentnce.', 'replace': 'A sentence.', 'reason': 'Spelling.'}],
      'more': True}),
    ('edit_document', 'Fix the spelling in the open document.',
     {'edits': [{'find': 'A sentnce.', 'replace': 'A sentence.'}]}),
    ('update_document', 'Rewrite the whole open document.', {'content': 'A sentence.'}),
])
async def test_editor_loop_recovers_scalar_arguments_and_emits_editor_event(monkeypatch, tool, prompt, args):
    import json
    import src.clean_agent_preview as runner
    from src.turn_contract import resolve_full_inventory_contract
    requests, executed = [], []
    deltas = iter([
        {'content': 'PREMATURE_SUCCESS', 'tool_calls': [{'index': 0, 'id': 'bad', 'function': {'name': tool, 'arguments': '123'}}]},
        {'tool_calls': [{'index': 0, 'id': 'good', 'function': {'name': tool, 'arguments': json.dumps(args)}}]},
        {'content': 'Done.'},
    ])
    class Response:
        def __init__(self, delta): self.delta = delta
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': self.delta}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(deltas))
    async def execute(block, **kwargs):
        executed.append(block)
        return tool, {'exit_code': 0, 'action': 'suggest' if tool == 'suggest_document' else 'update',
                      'doc_id': 'fixture', 'content': 'A sentence.', 'title': 'Fixture',
                      'language': 'markdown', 'version': 2,
                      'suggestions': args.get('suggestions', [])}
    import src.email_task_intent as intent_module
    async def classify(*args, **kwargs):
        return intent_module.EmailTaskIntent('other', (), 'Edit the open document')
    monkeypatch.setattr(intent_module, 'classify_email_task', classify)
    monkeypatch.setattr(runner.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(runner, 'execute_tool_block', execute)
    policy = ToolPolicy()
    # Preserve both direct writers; model chooses between targeted and full edit.
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in
               {'suggest_document', 'edit_document', 'update_document'}]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    doc = SimpleNamespace(id='fixture', title='Fixture', language='markdown', current_content='A sentnce.')
    events = [json.loads(c[6:]) async for c in runner.stream_preview(
        endpoint_url='http://test', model='Ajax', messages=[{'role': 'user', 'content': prompt}],
        headers={}, turn_contract=contract, session_id='fixture', owner='fixture',
        disabled_tools=set(), tool_policy=policy, active_document=doc, max_tokens=4096,
    ) if '[DONE]' not in c]
    assert len(executed) == 1
    assert requests[-1]["max_tokens"] == 256
    assert not requests[-1].get("tools")
    assert requests[0]['max_tokens'] == 4096
    assert requests[0]['tool_choice'] == 'auto'
    assert not any('PREMATURE_SUCCESS' in e.get('delta', '') for e in events)
    assert requests[0]['parallel_tool_calls'] is False
    assert 'parallel_tool_calls' not in requests[-1]
    outputs = [e for e in events if e.get('type') == 'tool_output']
    assert any(e.get('type') == 'editor_progress' for e in events)
    assert outputs[0]['execution_attempted'] is False
    assert 'JSON object' in outputs[0]['output']
    assert outputs[1]['error'] is False
    if tool == 'suggest_document':
        assert any(e.get('type') == 'doc_suggestions' for e in events)
    else:
        assert any(e.get('type') == 'doc_update' for e in events)


@pytest.mark.asyncio
async def test_finish_after_saved_editor_batch_skips_another_model_request(monkeypatch):
    import json
    import src.clean_agent_preview as runner
    from src.turn_contract import resolve_full_inventory_contract

    requests, executed = [], []
    args = {'edits': [{'find': 'A sentnce.', 'replace': 'A sentence.'}], 'more': True}

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *ignored): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [
                {'index': 0, 'id': 'edit', 'function': {
                    'name': 'edit_document', 'arguments': json.dumps(args)}}
            ]}}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **ignored): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *ignored): pass
        def stream(self, *ignored, **kwargs):
            requests.append(kwargs['json'])
            return Response()

    async def execute(block, **ignored):
        executed.append(block)
        return 'edit_document', {'exit_code': 0, 'action': 'edit', 'doc_id': 'fixture',
                                 'content': 'A sentence.', 'title': 'Fixture',
                                 'language': 'markdown', 'version': 2, 'applied': 1}

    import src.email_task_intent as intent_module
    async def classify(*ignored, **kwargs):
        return intent_module.EmailTaskIntent('other', (), 'Edit the open document')
    monkeypatch.setattr(intent_module, 'classify_email_task', classify)
    monkeypatch.setattr(runner.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(runner, 'execute_tool_block', execute)
    monkeypatch.setattr(runner.agent_runs, 'should_finish', lambda _session: bool(executed))
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'edit_document']
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    doc = SimpleNamespace(id='fixture', title='Fixture', language='markdown', current_content='A sentnce.')
    events = [json.loads(chunk[6:]) async for chunk in runner.stream_preview(
        endpoint_url='http://test', model='Ajax', messages=[{'role': 'user', 'content': 'Fix the spelling in the open document.'}],
        headers={}, turn_contract=contract, session_id='fixture', owner='fixture',
        disabled_tools=set(), tool_policy=policy, active_document=doc, max_tokens=4096,
    ) if chunk.startswith('data: ') and '[DONE]' not in chunk]
    assert len(requests) == 1
    assert len(executed) == 1
    assert any(e.get('type') == 'doc_update' for e in events)
    assert any(e.get('type') == 'final_response' and 'saved so far' in e.get('content', '')
               for e in events)


@pytest.mark.asyncio
async def test_finish_interrupts_wait_for_next_model_token():
    import asyncio
    from src.clean_agent_preview import preview_lines_until_finish

    finish = asyncio.Event()
    waiting = asyncio.Event()
    released = asyncio.Event()

    class SlowResponse:
        async def aiter_lines(self):
            waiting.set()
            try:
                await asyncio.Event().wait()
                yield 'unreachable'
            finally:
                released.set()

    task = asyncio.create_task(_collect_preview_lines(SlowResponse(), finish))
    await asyncio.wait_for(waiting.wait(), 1)
    finish.set()
    assert await asyncio.wait_for(task, 1) == []
    assert released.is_set()


async def _collect_preview_lines(response, finish):
    from src.clean_agent_preview import preview_lines_until_finish
    return [line async for line in preview_lines_until_finish(response, finish)]


def test_selection_only_edit_cannot_replace_all_occurrences():
    from src.clean_agent_preview import normalize_preview_call_args
    with pytest.raises(ValueError, match='Selection-only'):
        normalize_preview_call_args('edit_document', {'edits': [
            {'find': 'typo', 'replace': 'word', 'replace_all': True}]},
            user_text='Important scope: work only on this selected passage.')


def test_concise_suggestions_must_actually_shorten_prose():
    from src.clean_agent_preview import document_suggestion_quality_error
    prompt = ACTIONS['concise']
    original = '<p>This sentnce has an unecessary delay.</p>'
    spelling_only = '<p>This sentence has an unnecessary delay.</p>'
    proposed = {'suggestions': [{'find': original, 'replace': spelling_only}]}
    assert 'does not make its passage more concise' in document_suggestion_quality_error(
        'suggest_document', proposed, user_text=prompt)
    proposed['suggestions'][0]['replace'] = '<p>This sentence drags.</p>'
    assert document_suggestion_quality_error('suggest_document', proposed, user_text=prompt) is None
    proposed['suggestions'][0]['replace'] = spelling_only
    assert document_suggestion_quality_error('suggest_document', proposed,
        user_text='Proofread the open document.') is None


def test_ajax_editor_schema_limits_batches_and_exposes_continuation():
    from src.clean_agent_preview import compact_schemas
    schemas = compact_schemas([s for s in FUNCTION_TOOL_SCHEMAS
        if s['function']['name'] in {'edit_document', 'suggest_document'}], model='Ajax')
    by_name = {s['function']['name']: s['function']['parameters']['properties'] for s in schemas}
    assert by_name['edit_document']['edits']['maxItems'] == 12
    assert by_name['suggest_document']['suggestions']['maxItems'] == 12
    assert by_name['edit_document']['more']['type'] == 'boolean'
    assert by_name['suggest_document']['more']['type'] == 'boolean'


def test_exact_edits_continue_but_suggestions_finish_after_one_bounded_set():
    from src.clean_agent_preview import editor_batch_continues
    assert editor_batch_continues('edit_document', {'edits': [{}] * 12})
    assert not editor_batch_continues('suggest_document', {'suggestions': [{}] * 12})
    assert not editor_batch_continues('suggest_document', {'suggestions': [{}], 'more': True})
    assert not editor_batch_continues('edit_document', {'edits': [{}] * 11})
    assert not editor_batch_continues('edit_document', {'edits': [{}] * 12, 'more': False})
    assert editor_batch_continues('edit_document', {'edits': [{}], 'more': True})


def test_noop_sibling_does_not_create_a_failed_tool_card_after_a_real_edit():
    import json
    from src.clean_agent_preview import drop_redundant_editor_noops
    good = {'function': {'name': 'edit_document', 'arguments': json.dumps({
        'edits': [{'find': 'old', 'replace': 'new'}]})}}
    noop = {'function': {'name': 'edit_document', 'arguments': json.dumps({
        'edits': [{'find': 'already correct', 'replace': 'already correct'}]})}}
    assert drop_redundant_editor_noops([good, noop]) == [good]
    assert drop_redundant_editor_noops([noop]) == [noop]
    assert drop_redundant_editor_noops([good, good]) == [good]
