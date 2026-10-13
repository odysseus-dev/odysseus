from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
import json
import jsonschema
import pytest
import re

from src.clean_agent_preview import conversation, readonly_call, preview_call_allowed, evaluate_preview_call, authorized_write_families, compact_schemas, normalize_preview_function_args, normalize_preview_call_args, private_browser_dom_batch, private_browser_state_transition, private_browser_success_repeat_limit, stream_preview, denied_response, execution_has_write_effect, requests_mutation, claims_completion, recent_successful_write_families, scope_preview_contract, multimodal_image_count, attachment_reference_count, active_document_context_message, active_email_context_message, targets_active_editor, active_editor_whole_draft_request, active_editor_suggestion_request, scope_active_editor_contract, native_execution_limits, interactive_execution_limit, runtime_required_artifacts, execution_targets_required_artifact, artifact_completion_python_code_error, artifact_completion_tool_schemas, required_artifact_completion_tool_choice, document_suggestions_event, document_suggestion_quality_error, required_read_tool_choice, required_active_editor_tool_choice, sealed_read_arguments, email_identifier_error, requested_item_limit, contract_item_limit, notes_terminal_response, documents_terminal_response, shell_listing_terminal_response, shell_output_terminal_response, direct_shell_output_request, ui_panel_terminal_response, ui_toggle_state_result, calendar_terminal_response, memory_terminal_response, tasks_terminal_response, task_list_requires_synthesis, skills_terminal_response, cookbook_servers_terminal_response, prior_short_answer_for_no_tool_summary, prior_collection_repeat_answer, prior_failed_operation_answer, prior_cookbook_server_answer, prior_workspace_path_answer, prior_web_source_answer, bounded_web_evidence_answer, inherit_referential_read_arguments, normalized_search_intent, requested_web_source_links, web_source_links, requested_web_link_limit, preserve_requested_web_recency, ground_referenced_note_content, note_search_result_empty, note_referent_error, research_referent_error, private_browser_open_url, private_browser_effective_url, web_fetch_observation_is_boilerplate, broad_current_web_request, record_tool_execution, align_structured_tool_history, provider_request_messages, provider_compatible_tool_choice_request, offered_tool_alias, dependent_write_prerequisite_error, bounded_research_tool_policy, retrieved_source_urls, serialize_required_email_attachment_chain
from src.tool_capabilities import capabilities_for_tool


def test_native_python_write_command_counts_as_successful_write_effect():
    assert execution_has_write_effect(
        "python",
        {"code": "open('/workspace/results/out.txt', 'w').write('ok')"},
        capabilities_for_tool("python"),
        native_workspace_enabled=True,
    )


def test_read_only_code_does_not_count_as_successful_write_effect():
    assert not execution_has_write_effect(
        "bash",
        {"command": "cat /workspace/input.txt"},
        capabilities_for_tool("bash"),
        native_workspace_enabled=True,
    )


def test_code_mutation_is_not_promoted_outside_native_workspace():
    assert not execution_has_write_effect(
        "bash",
        {"command": "printf ok > /workspace/results/out.txt"},
        capabilities_for_tool("bash"),
        native_workspace_enabled=False,
    )


def test_create_draft_alias_resolves_only_when_reviewable_draft_is_offered():
    offered = [{'function': {'name': 'mcp__email__draft_email'}}]
    assert offered_tool_alias('mcp__email__create_draft', offered) == 'mcp__email__draft_email'
    assert offered_tool_alias('mcp__email__create_draft', []) == 'mcp__email__create_draft'


def test_calendar_dependent_draft_requires_successful_calendar_evidence():
    class Contract:
        required_read_operation = type('Operation', (), {'tool': 'manage_calendar'})()

    assert dependent_write_prerequisite_error(Contract(), 'mcp__email__draft_email', set())
    assert dependent_write_prerequisite_error(
        Contract(), 'mcp__email__draft_email', {'manage_calendar'}
    ) is None

    class RequiredSetContract:
        required_read_operation = None
        required = {'manage_calendar', 'mcp__email__draft_email'}

    assert dependent_write_prerequisite_error(
        RequiredSetContract(), 'mcp__email__draft_email', set()
    )


def test_required_email_attachment_batch_is_serialized_by_successful_stage():
    required = {'search_emails', 'read_email', 'download_attachment', 'draft_email'}
    calls = [
        {'function': {'name': f'mcp__email__{name}', 'arguments': '{}'}}
        for name in ('search_emails', 'read_email', 'download_attachment', 'draft_email')
    ]
    assert serialize_required_email_attachment_chain(calls, required, [])[0]['function']['name'].endswith('search_emails')
    done = [{'tool': 'mcp__email__search_emails', 'exit_code': 0, 'error': False}]
    assert serialize_required_email_attachment_chain(calls, required, done)[0]['function']['name'].endswith('read_email')


def test_provider_request_messages_strips_internal_metadata_without_mutating_history():
    history = [{
        'role': 'user',
        'content': [{'type': 'text', 'text': 'evidence'}],
        'metadata': {'trusted': False, 'source': 'tool visual evidence'},
    }]

    assert provider_request_messages(history) == [{
        'role': 'user',
        'content': [{'type': 'text', 'text': 'evidence'}],
    }]
    assert history[0]['metadata']['trusted'] is False


def test_provider_wire_messages_drops_empty_assistant_placeholder():
    from src.clean_agent_preview import provider_wire_messages

    history = [
        {'role': 'assistant', 'content': None},
        {'role': 'user', 'content': 'Completion check: create the artifact.'},
    ]

    assert provider_request_messages(history) == history
    assert provider_wire_messages(history) == [history[1]]


def test_deepseek_flash_keeps_tools_but_drops_unsupported_forced_choice():
    request = {
        'model': 'deepseek-flash',
        'tools': [
            {'type': 'function', 'function': {'name': 'inspect_media'}},
            {'type': 'function', 'function': {'name': 'python'}},
        ],
        'tool_choice': {'type': 'function', 'function': {'name': 'inspect_media'}},
    }

    compatible = provider_compatible_tool_choice_request(request, 'deepseek-flash')
    assert [tool['function']['name'] for tool in compatible['tools']] == ['inspect_media']
    assert 'tool_choice' not in compatible
    assert request['tool_choice']['function']['name'] == 'inspect_media'
    assert provider_compatible_tool_choice_request(request, 'qwen3.5-9b') is request


def test_deepseek_v4_flash_drops_named_choice_for_thinking_mode():
    request = {
        'tools': [{'type': 'function', 'function': {'name': 'write_file'}}],
        'tool_choice': {'type': 'function', 'function': {'name': 'write_file'}},
    }
    compatible = provider_compatible_tool_choice_request(request, 'deepseek-v4-flash')
    assert [tool['function']['name'] for tool in compatible['tools']] == ['write_file']
    assert 'tool_choice' not in compatible


def test_kimi_k3_drops_named_choice_incompatible_with_thinking_mode():
    request = {
        'tools': [
            {'type': 'function', 'function': {'name': 'inspect_media'}},
            {'type': 'function', 'function': {'name': 'write_file'}},
        ],
        'tool_choice': {'type': 'function', 'function': {'name': 'inspect_media'}},
    }

    compatible = provider_compatible_tool_choice_request(request, 'kimi-k3')

    assert [tool['function']['name'] for tool in compatible['tools']] == [
        'inspect_media'
    ]
    assert 'tool_choice' not in compatible


def test_qwen_capture_converts_single_required_tool_to_named_constraint():
    request = {
        'tools': [{'type': 'function', 'function': {'name': 'web_search'}}],
        'tool_choice': 'required',
    }
    compatible = provider_compatible_tool_choice_request(
        request, 'odysseus-qwen3.5-tools-pre-heretic'
    )
    assert compatible['tool_choice'] == {
        'type': 'function', 'function': {'name': 'web_search'},
    }


def test_private_browser_observations_do_not_advance_page_revision():
    assert private_browser_state_transition({'action': 'snapshot'}, 'https://example.org') == (
        False, 'https://example.org')
    assert private_browser_state_transition({'action': 'find', 'text': 'heading'}, None) == (
        False, None)
    assert private_browser_state_transition(
        {'action': 'batch', 'commands': [['snapshot'], ['snapshot']]},
        'https://example.org',
    ) == (False, 'https://example.org')


def test_private_browser_interactions_and_new_navigation_advance_page_revision():
    assert private_browser_state_transition(
        {'action': 'open', 'url': 'https://example.org'}, None,
    ) == (True, 'https://example.org')
    assert private_browser_state_transition(
        {'action': 'open', 'url': 'https://example.org'}, 'https://example.org',
    ) == (False, 'https://example.org')
    assert private_browser_state_transition(
        {'action': 'click', 'target': '@e2'}, 'https://example.org',
    ) == (True, 'https://example.org')
    assert private_browser_state_transition(
        {'action': 'click', 'target': '@e2'}, 'https://example.org',
        {'exit_code': 1, 'error': None, 'output': 'Unknown ref: e2'},
    ) == (False, 'https://example.org')


def test_private_browser_snapshot_repeat_limit_is_bounded():
    assert private_browser_success_repeat_limit({'action': 'snapshot'}) == 3
    assert private_browser_success_repeat_limit(
        {'action': 'batch', 'commands': [['snapshot'], ['snapshot']]},
    ) == 3
    assert private_browser_success_repeat_limit({'action': 'open', 'url': 'https://example.org'}) == 1


def test_private_browser_covered_click_does_not_advance_dom_revision():
    changed, current_url = private_browser_state_transition(
        {'action': 'click', 'target': '@e100'},
        'https://www.ikea.com/',
        {
            'exit_code': 1,
            'output': (
                "Element '@e100' is covered by <main#main> at its click point, "
                'so the input would land on that element instead.'
            ),
        },
    )

    assert changed is False
    assert current_url == 'https://www.ikea.com/'


def test_compact_notes_preserves_create_vs_edit_and_replacement_semantics():
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    compact = compact_schemas([schema])[0]['function']
    assert 'add creates a new note' in compact['description']
    assert 'update with id' in compact['description']
    assert 'replaces the whole checklist' in compact['parameters']['properties']['checklist_items']['description']


def test_compact_notes_exposes_optional_explicit_checked_state():
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    for candidate in (schema, compact_schemas([schema])[0]):
        parameters = candidate['function']['parameters']
        assert parameters['properties']['done']['type'] == 'boolean'
        assert 'omit to toggle' in parameters['properties']['done']['description']
        assert 'done' not in parameters['required']


def test_compact_skills_distinguishes_field_edits_from_raw_text_patches():
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_skills')
    compact = compact_schemas([schema])[0]['function']
    props = compact['parameters']['properties']
    assert props['procedure']['type'] == 'array'
    assert 'add/edit' in props['procedure']['description']
    assert 'not a flag' in props['procedure']['description']
    assert 'exactly once' in props['old_string']['description']
    assert 'full SKILL.md' in props['old_string']['description']


def test_compact_skills_preserves_reference_read_contract():
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_skills')
    params = compact_schemas([schema])[0]['function']['parameters']
    props = params['properties']
    assert 'view = SKILL.md' in props['action']['description']
    assert 'view_ref = supporting file' in props['action']['description']
    assert 'not a file path' in props['name']['description']
    assert 'view_ref only' in props['path']['description']
    assert params['required'] == ['action']  # listing still needs no name/path


@pytest.mark.parametrize('name,key', [
    ('edit_document', 'edits'),
    ('suggest_document', 'suggestions'),
])
def test_preview_normalizes_json_encoded_document_arrays_before_schema_validation(name, key):
    value = [{'find': 'old', 'replace': 'new'}]
    if name == 'suggest_document':
        value[0]['reason'] = 'clearer'

    tool_type, normalized = normalize_preview_function_args(
        name,
        {key: json.dumps(value)},
        user_text='Improve the active draft.',
    )

    assert tool_type == name
    assert normalized[key] == value
    schema = next(
        item for item in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if item['function']['name'] == name
    )
    jsonschema.validate(normalized, schema['function']['parameters'])


@pytest.mark.parametrize('message', [
    'run that list again, three only, read-only',
    'do that again pls, just three, not touching anything',
])
def test_explicit_operation_repeat_is_not_replaced_with_stale_collection(message):
    history = [{
        'role': 'assistant',
        'tool_calls': [{
            'id': 'call-1', 'type': 'function',
            'function': {'name': 'manage_documents', 'arguments': '{"action":"list"}'},
        }],
    }, {
        'role': 'tool', 'tool_call_id': 'call-1',
        'content': '{"response":"Old rows","exit_code":0}',
    }]

    assert prior_collection_repeat_answer(message, history) == ''


@pytest.mark.parametrize(('name', 'field'), [
    ('manage_documents', 'limit'),
])
def test_model_choice_runtime_still_normalizes_transport_integer_strings(name, field):
    tool_type, normalized = normalize_preview_call_args(
        name, {'action': 'list', field: '3'},
        user_text='list three', model_choice_experiment=True,
    )

    assert tool_type == name
    assert normalized[field] == 3


def test_model_choice_drops_unsupported_task_list_limit_alias():
    tool_type, normalized = normalize_preview_call_args(
        'manage_tasks', {'action': 'list', 'max_results': '3'},
        user_text='list three', model_choice_experiment=True,
    )

    assert tool_type == 'manage_tasks'
    assert normalized == {'action': 'list'}


def test_contract_sealed_hwfit_get_is_allowed_but_generic_app_api_is_not():
    args = {
        'action': 'call',
        'method': 'GET',
        'path': '/api/hwfit/models?fit_only=true&limit=10&sort=fit',
    }
    allowed = evaluate_preview_call(
        'app_api', args, 'find the best model to run on my hardware',
        contract_required_tools={'app_api'},
        turn_authorized_families={'cookbook_admin'},
    )
    assert allowed.allowed

    denied = evaluate_preview_call(
        'app_api', {'action': 'call', 'method': 'GET', 'path': '/api/cookbook/state'},
        'show cookbook state', contract_required_tools={'app_api'},
        turn_authorized_families={'cookbook_admin'},
    )
    assert not denied.allowed


def test_contract_sealed_gallery_read_and_explicit_image_edit_are_allowed():
    gallery = evaluate_preview_call(
        'app_api', {
            'action': 'call', 'method': 'GET', 'path': '/api/gallery/library',
        },
        'list my gallery images through the internal app api',
        contract_required_tools={'app_api'},
        turn_authorized_families={'cookbook_admin'},
    )
    assert gallery.allowed
    upscale = evaluate_preview_call(
        'edit_image', {'image_id': 'owned-image', 'action': 'upscale', 'scale': 2},
        'upscale that image 2x',
        contract_required_tools={'edit_image'},
        turn_authorized_families={'image_editing'},
        model_choice_private_tools={'edit_image'},
    )
    assert upscale.allowed


def test_compact_suggestion_contract_forbids_noop_replacements():
    schema = next(
        item for item in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if item['function']['name'] == 'suggest_document'
    )['function']

    assert 'never emit a no-op suggestion' in schema['description']
    replace = schema['parameters']['properties']['suggestions']['items']['properties']['replace']
    assert 'MUST be materially different from find' in replace['description']


def test_interactive_ocr_uses_upload_references_not_arbitrary_workspace_reads():
    owned_ref = evaluate_preview_call('extract_text', {'path': 'odysseus://attachment/fixture-upload'}, 'OCR this image')
    assert owned_ref.allowed
    assert owned_ref.effects == ('read_private',)
    assert not evaluate_preview_call('extract_text', {'path': '/etc/passwd'}, 'OCR this image').allowed
    assert not evaluate_preview_call('extract_text', {'path': '/workspace/image.png'}, 'OCR this image').allowed
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract


def test_native_execution_limits_allow_multi_artifact_work_without_unbounded_rounds():
    assert native_execution_limits(64) == (64, 32)
    assert native_execution_limits(1000) == (64, 32)
    assert native_execution_limits("invalid") == (8, 32)


def test_compact_preview_honors_configured_interactive_round_limit():
    # A configured budget is the user's explicit instruction, honored up to
    # the same 200 ceiling the settings endpoint enforces.
    assert interactive_execution_limit(100) == 100
    assert interactive_execution_limit(1000) == 200
    assert interactive_execution_limit(0) == 1
    # No resolvable budget falls back to the bounded default.
    assert interactive_execution_limit(None) == 8
    assert interactive_execution_limit("invalid") == 8


def test_compact_preview_honors_configured_tool_call_budget():
    from src.clean_agent_preview import (
        INTERACTIVE_BROWSER_TOOL_CALL_LIMIT,
        INTERACTIVE_TOOL_CALL_LIMIT,
        UNLIMITED_TOOL_CALL_LIMIT,
        interactive_tool_call_limit,
    )
    # 0 means unlimited, matching the main agent loop's max_tool_calls <= 0.
    assert interactive_tool_call_limit(0) == UNLIMITED_TOOL_CALL_LIMIT
    assert interactive_tool_call_limit(0, browser_offered=True) == UNLIMITED_TOOL_CALL_LIMIT
    # An explicit finite budget is honored in both directions.
    assert interactive_tool_call_limit(5) == 5
    assert interactive_tool_call_limit(250) == 250
    # A malformed value keeps the bounded default.
    assert interactive_tool_call_limit("invalid") == INTERACTIVE_TOOL_CALL_LIMIT
    assert interactive_tool_call_limit(None, browser_offered=True) == (
        INTERACTIVE_BROWSER_TOOL_CALL_LIMIT
    )


@pytest.mark.parametrize('text,expected', [
    ('helo', True), ('Hello!', True), ('thanks', True),
    ('hello, find the latest news', False), ('thanks, now open the source', False),
    ('search for the song Hello', False),
])
def test_social_turn_requires_the_entire_request(text, expected):
    from src.clean_agent_preview import standalone_social_turn
    assert standalone_social_turn(text) is expected


@pytest.mark.parametrize('prompt,expected', [
    ('Read /workspace/fixtures/paper.pdf and create /workspace/results.csv and /workspace/chart.png',
     ('/workspace/results.csv', '/workspace/chart.png')),
    ('Create /workspace/results.csv using /workspace/source.csv', ('/workspace/results.csv',)),
    ('Create /workspace/results.csv. Read /workspace/source.csv and /workspace/other.csv',
     ('/workspace/results.csv',)),
    ('Read /workspace/source.csv and /workspace/other.csv', ()),
    ('Create /workspace/results.v2.csv and /workspace/chart.v2.png',
     ('/workspace/results.v2.csv', '/workspace/chart.v2.png')),
])
def test_all_requested_outputs_survive_filename_periods(prompt, expected):
    from src.clean_agent_preview import declared_workspace_artifacts
    assert declared_workspace_artifacts(prompt) == expected


def test_notes_terminal_response_preserves_links_from_wrapped_executor_output():
    from src.clean_agent_preview import notes_terminal_response

    raw = json.dumps({
        'results': '- [note-123] **Fixture note**',
        'exit_code': 0,
    })
    assert notes_terminal_response(raw) == (
        'Here are your notes (1):\n📝 [Fixture note](#note-note-123)'
    )


@pytest.mark.parametrize('tool,field,anchor,expected', [
    ('manage_notes', 'id', '#note-note-123', 'note-123'),
    ('manage_calendar', 'uid', '#event-event-123', 'event-123'),
    ('manage_tasks', 'task_id', '#task-task-123', 'task-123'),
    ('manage_memory', 'memory_id', '#memory-memory-123', 'memory-123'),
    ('manage_documents', 'document_id', '#document-document-123', 'document-123'),
    ('read_email', 'uid', '#email-104', '104'),
])
def test_clickable_entity_anchor_is_normalized_back_to_its_server_id(
    tool, field, anchor, expected,
):
    _, args = normalize_preview_function_args(tool, {field: anchor})
    assert args[field] == expected


def test_successful_document_suggestion_has_one_browser_owned_event():
    suggestions = [{'find': 'wordy', 'replace': 'concise', 'reason': 'clarity'}]
    assert document_suggestions_event({'doc_id': 'doc-1', 'suggestions': suggestions}) == {
        'type': 'doc_suggestions', 'doc_id': 'doc-1', 'suggestions': suggestions,
    }
    assert document_suggestions_event(
        {'doc_id': 'doc-1', 'suggestions': suggestions}, failed=True,
    ) is None
    assert document_suggestions_event({'error': 'no match'}, failed=True) is None


def test_preserve_meaning_rejects_repeated_destructive_suggestion_replacement():
    args = {'suggestions': [
        {'find': 'First passage ' * 12, 'replace': 'This book is fiction.', 'reason': 'Concise.'},
        {'find': 'Different second passage ' * 8, 'replace': 'This book is fiction.', 'reason': 'Concise.'},
    ]}
    assert document_suggestion_quality_error(
        'suggest_document', args, user_text='Rewrite this but preserve the meaning.'
    )
    assert document_suggestion_quality_error(
        'suggest_document', args, user_text='Make suggestions.'
    ) is None


def test_runtime_required_artifacts_includes_runner_declared_directory():
    assert runtime_required_artifacts(
        'Create the requested output.',
        {'completion_requirements': {'required_artifacts': ['/tmp_workspace/results/']}},
    ) == ('/tmp_workspace/results',)


def test_required_artifact_content_rejects_empty_directories(tmp_path):
    import src.clean_agent_preview as module

    output = tmp_path / 'results'
    output.mkdir()
    (output / 'nested-empty').mkdir()

    assert not module.required_artifacts_have_content((str(output),))


def test_required_artifact_content_accepts_nonempty_files_and_nested_outputs(tmp_path):
    import src.clean_agent_preview as module

    output_file = tmp_path / 'output.html'
    output_file.write_text('<h1>done</h1>')
    output_dir = tmp_path / 'results'
    nested = output_dir / 'part-01'
    nested.mkdir(parents=True)
    (nested / 'answer.json').write_text('{"done": true}')

    assert module.required_artifacts_have_content((str(output_file),))
    assert module.required_artifacts_have_content((str(output_dir),))
    assert not module.required_artifacts_have_content((str(tmp_path / 'missing'),))


def test_required_artifact_mutation_trusts_exact_files_but_not_empty_directory_setup(tmp_path):
    import src.clean_agent_preview as module

    output_dir = tmp_path / 'results'
    output_dir.mkdir()
    assert not module.successful_required_artifact_mutation(
        'bash', {'command': f'mkdir -p {output_dir}'}, (str(output_dir),),
        {'workspace_mutated': True, 'mutated_artifacts': [str(output_dir)]},
    )
    assert module.successful_required_artifact_mutation(
        'python', {'code': f"open('{output_dir}/1.tex', 'w').write('table')"},
        (str(output_dir),),
        {'materialized_artifacts': [str(output_dir)]},
    )
    output_file = tmp_path / 'output.html'
    assert module.successful_required_artifact_mutation(
        'write_file', {'path': str(output_file), 'content': '<h1>done</h1>'},
        (str(output_file),),
    )


@pytest.mark.asyncio
async def test_empty_required_directory_keeps_native_turn_running(monkeypatch, tmp_path):
    import src.clean_agent_preview as module

    output = tmp_path / 'results'
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0,
            'id': 'call-mkdir',
            'type': 'function',
            'function': {
                'name': 'bash',
                'arguments': json.dumps({'command': f'mkdir -p {output}'}),
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0,
            'id': 'call-write',
            'type': 'function',
            'function': {
                'name': 'write_file',
                'arguments': json.dumps({
                    'path': str(output / 'answer.txt'), 'content': 'done',
                }),
            },
        }]}}]},
        {'choices': [{'delta': {'content': 'Completed the requested output.'}}]},
    ])
    requests = []
    executed = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        if block.tool_type == 'bash':
            output.mkdir()
        else:
            (output / 'answer.txt').write_text('done')
        return block.tool_type, {'output': '(no output)', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] in {'bash', 'write_file'}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='qwen-test',
        messages=[{'role': 'user', 'content': 'Create the requested output files.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace=str(tmp_path),
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {'required_artifacts': [str(output)]},
        }, max_rounds=8,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert executed == ['bash', 'write_file']
    after_mkdir = next(
        event for event in events
        if event.get('type') == 'agent_step' and event.get('calls_used') == 1
    )
    assert after_mkdir['required_artifact_pending'] is True
    assert (output / 'answer.txt').read_text() == 'done'


def test_artifact_completion_schema_binds_single_required_file_without_mutating_source():
    source = [{
        'type': 'function',
        'function': {
            'name': 'write_file',
            'description': 'Write a file.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content': {'type': 'string'},
                },
                'required': ['path', 'content'],
            },
        },
    }]

    bound = artifact_completion_tool_schemas(
        source, ('/workspace/output.html',),
    )

    path_schema = bound[0]['function']['parameters']['properties']['path']
    assert path_schema['const'] == '/workspace/output.html'
    assert '/workspace/output.html' in path_schema['description']
    assert 'const' not in source[0]['function']['parameters']['properties']['path']


def test_artifact_completion_schema_binds_directory_descendant_but_not_multiple_outputs():
    source = [
        {
            'type': 'function',
            'function': {
                'name': 'write_file',
                'parameters': {
                    'type': 'object',
                    'properties': {'path': {'type': 'string'}},
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'python',
                'parameters': {
                    'type': 'object',
                    'properties': {'code': {'type': 'string'}},
                },
            },
        },
    ]

    directory_bound = artifact_completion_tool_schemas(
        source, ('/workspace/results/',),
    )
    directory_path = directory_bound[0]['function']['parameters']['properties']['path']
    assert re.search(directory_path['pattern'], '/workspace/results/output.md')
    assert not re.search(directory_path['pattern'], '/workspace/results')
    assert 'inside the required directory' in directory_path['description']
    assert 'pattern' not in source[0]['function']['parameters']['properties']['path']
    directory_code = directory_bound[1]['function']['parameters']['properties']['code']
    assert 'pattern' not in directory_code
    assert 'non-empty files inside' in directory_code['description']
    assert 'Complete executable Python' in directory_code['description']
    assert 'not only a path string' in directory_code['description']
    assert artifact_completion_tool_schemas(
        source, ('/workspace/a.txt', '/workspace/b.txt'),
    ) == source


def test_artifact_completion_python_code_requires_valid_code_and_output_reference():
    required = ('/workspace/results',)

    assert 'valid executable Python' in artifact_completion_python_code_error(
        {'code': '/workspace/results/'}, required,
    )
    assert 'required output directory' in artifact_completion_python_code_error(
        {'code': "Path('/workspace/source.tar').unlink()"}, required,
    )
    assert artifact_completion_python_code_error(
        {'code': (
            "from pathlib import Path\n"
            "p = Path('/workspace/results/1.tex')\n"
            "p.write_text('table')"
        )},
        required,
    ) == ''


def test_required_binary_artifact_forces_python_instead_of_text_writer():
    offered = [
        {'type': 'function', 'function': {'name': 'write_file'}},
        {'type': 'function', 'function': {
            'name': 'python',
            'parameters': {
                'type': 'object',
                'properties': {'code': {'type': 'string'}},
                'required': ['code'],
            },
        }},
    ]

    assert required_artifact_completion_tool_choice(
        ('/workspace/output.png',), offered,
    ) == {'type': 'function', 'function': {'name': 'python'}}
    assert required_artifact_completion_tool_choice(
        ('/workspace/output.html',), offered,
    ) == {'type': 'function', 'function': {'name': 'write_file'}}
    assert required_artifact_completion_tool_choice(
        ('/workspace/output.png',), offered[:1],
    ) is None
    binary_bound = artifact_completion_tool_schemas(
        offered, ('/workspace/output.png',),
    )
    assert [schema['function']['name'] for schema in binary_bound] == ['python']
    code_schema = binary_bound[0]['function']['parameters']['properties']['code']
    assert 'pattern' not in code_schema
    assert 'Complete executable Python' in code_schema['description']
    assert 'not only a path string' in code_schema['description']
    assert '/workspace/output.png' in code_schema['description']
    assert [schema['function']['name'] for schema in offered] == [
        'write_file', 'python',
    ]


def test_required_directory_artifact_allows_any_offered_mutation_tool():
    offered = [
        {'type': 'function', 'function': {'name': 'bash'}},
        {'type': 'function', 'function': {'name': 'write_file'}},
    ]

    assert required_artifact_completion_tool_choice(
        ('/tmp_workspace/results',), offered,
    ) == 'required'
    offered.append({'type': 'function', 'function': {'name': 'python'}})
    assert required_artifact_completion_tool_choice(
        ('/tmp_workspace/results',), offered,
    ) == {'type': 'function', 'function': {'name': 'python'}}


def test_action_promise_response_rejects_future_work_but_not_real_answers():
    import src.clean_agent_preview as module

    assert module.action_promise_response(
        'Let me extract frames and read the on-screen text directly.'
    )
    assert module.action_promise_response(
        'I will now inspect the remaining segments before answering.'
    )
    assert not module.action_promise_response(
        'I inspected all segments. Alex served: 6, Sam served: 6.'
    )
    assert not module.action_promise_response(
        'Alex served 6 times. Let me know if you want the timestamps.'
    )


def test_repeated_off_contract_artifact_calls_trigger_single_file_body_handoff():
    import src.clean_agent_preview as module

    assert module.repeated_off_contract_artifact_handoff_target(
        artifact_write_phase=True,
        successful_artifact_write=False,
        required_artifacts=('/workspace/output.html',),
        failures=2,
    ) == '/workspace/output.html'

    assert not module.repeated_off_contract_artifact_handoff_target(
        artifact_write_phase=True,
        successful_artifact_write=False,
        required_artifacts=('/workspace/output.html',),
        failures=1,
    )
    assert not module.repeated_off_contract_artifact_handoff_target(
        artifact_write_phase=True,
        successful_artifact_write=False,
        required_artifacts=('/workspace/a.html', '/workspace/b.html'),
        failures=2,
    )
    assert not module.repeated_off_contract_artifact_handoff_target(
        artifact_write_phase=True,
        successful_artifact_write=False,
        required_artifacts=('/workspace/results',),
        failures=2,
    )


def test_provider_wire_drops_deepseek_reasoning_only_turn_rejected_by_provider():
    import src.clean_agent_preview as module

    messages = [{
        'role': 'assistant',
        'content': None,
        'reasoning_content': 'private provider reasoning token stream',
    }]

    assert module.provider_wire_messages(messages) == []


@pytest.mark.asyncio
async def test_empty_artifact_writer_turn_recovers_via_body_handoff(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'reasoning_content': 'spent the turn planning'}}]},
        {'choices': [{'delta': {'content': '<html><body>Recovered</body></html>'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block)
        return block.tool_type, {'output': 'written', 'exit_code': 0}

    monkeypatch.setattr(module, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 0)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'write_file'
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='deepseek-flash',
        messages=[{'role': 'user', 'content': 'Create the requested HTML.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {
                'required_artifacts': ['/workspace/output.html'],
            },
        }, max_tokens=8192, max_rounds=3,
    )]

    assert len(requests) == 2
    assert requests[1]['max_tokens'] == 8192
    assert [block.tool_type for block in executed] == ['write_file']
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(event.get('type') == 'artifact_body_handoff' for event in events)


@pytest.mark.asyncio
async def test_malformed_writer_body_handoff_reopens_writer_after_two_tool_violations(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'malformed-write',
            'function': {
                'name': 'write_file',
                'arguments': '{"path":"/workspace/output.tex"}\n{"content":"truncated"}',
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'off-contract-bash-one',
            'function': {
                'name': 'bash',
                'arguments': json.dumps({'command': 'echo nope > /workspace/output.tex'}),
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'off-contract-bash-two',
            'function': {
                'name': 'bash',
                'arguments': json.dumps({'command': 'echo nope > /workspace/output.tex'}),
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'recovered-write',
            'function': {
                'name': 'write_file',
                'arguments': json.dumps({
                    'path': '/workspace/output.tex',
                    'content': '\\begin{tabular}{ll}A & B\\\\\\end{tabular}',
                }),
            },
        }]}}]},
        {'choices': [{'delta': {'content': 'Created the requested TeX artifact.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block)
        return block.tool_type, {'output': 'written', 'exit_code': 0}

    monkeypatch.setattr(module, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 0)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] in {'write_file', 'bash'}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Create the requested TeX table.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {
                'required_artifacts': ['/workspace/output.tex'],
            },
        }, max_tokens=8192, max_rounds=6,
    )]

    assert len(requests) == 5
    assert 'tools' not in requests[1]
    assert 'tools' not in requests[2]
    assert requests[3]['tool_choice'] == {
        'type': 'function', 'function': {'name': 'write_file'},
    }
    assert [block.tool_type for block in executed] == ['write_file']
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert sum(
        event.get('reason') == 'malformed_write_body_handoff'
        for event in events
    ) == 2
    assert any(
        event.get('reason') == 'malformed_write_body_handoff_exhausted'
        for event in events
    )
    assert any(
        event.get('type') == 'final_response'
        and event.get('content') == 'Created the requested TeX artifact.'
        for event in events
    )


@pytest.mark.asyncio
async def test_concatenated_writer_arguments_execute_as_bounded_ordered_calls(monkeypatch):
    import src.clean_agent_preview as module

    concatenated = ''.join(json.dumps(item) for item in (
        {'path': '/workspace/results/1.tex', 'content': '1.tex'},
        {'path': '/workspace/results/1.tex', 'content': '\\begin{table}One\\end{table}'},
        {'path': '/workspace/results/2.tex', 'content': '2.tex'},
        {'path': '/workspace/results/2.tex', 'content': '\\begin{table}Two\\end{table}'},
    ))
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'concatenated-writes',
            'function': {'name': 'write_file', 'arguments': concatenated},
        }]}}]},
        {'choices': [{'delta': {'content': 'Created the requested TeX files.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        path, content = block.content.split('\n', 1)
        executed.append({'path': path, 'content': content})
        return block.tool_type, {'output': 'written', 'exit_code': 0}

    monkeypatch.setattr(module, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 0)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'write_file'
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Extract every table into the results directory.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {
                'required_artifacts': ['/workspace/results'],
            },
        }, max_tokens=8192, max_rounds=2,
    )]

    assert len(requests) == 2
    assert executed == [
        {'path': '/workspace/results/1.tex', 'content': '1.tex'},
        {'path': '/workspace/results/1.tex', 'content': '\\begin{table}One\\end{table}'},
        {'path': '/workspace/results/2.tex', 'content': '2.tex'},
        {'path': '/workspace/results/2.tex', 'content': '\\begin{table}Two\\end{table}'},
    ]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(
        event.get('type') == 'tool_argument_recovery'
        and event.get('format') == 'concatenated_json_objects'
        and event.get('calls') == 4
        for event in events
    )
    assert not any(
        event.get('reason') == 'malformed_write_body_handoff'
        for event in events
    )


def test_concatenated_writer_recovery_fails_closed_for_ambiguous_or_large_batches():
    from src.clean_agent_preview import expand_concatenated_write_calls

    ambiguous = {
        'id': 'ambiguous', 'type': 'function', 'function': {
            'name': 'write_file',
            'arguments': '{"path":"/workspace/a","content":"a"}{"path":"/workspace/b","mode":"x"}',
        },
    }
    too_large = {
        'id': 'large', 'type': 'function', 'function': {
            'name': 'write_file',
            'arguments': ''.join(
                json.dumps({'path': f'/workspace/{index}', 'content': 'x'})
                for index in range(17)
            ),
        },
    }

    assert expand_concatenated_write_calls([ambiguous]) == ([ambiguous], 0)
    assert expand_concatenated_write_calls([too_large]) == ([too_large], 0)


def test_mixed_writer_argument_recovers_only_grounded_leading_json_write():
    from src.clean_agent_preview import expand_concatenated_write_calls

    first = {'path': '/workspace/results/1.tex', 'content': 'grounded table'}
    trailing = '''
<tool_call>
<function=bash>
<parameter=command>sed -n '2,3p' /workspace/paper.tex</parameter>
</function>
</tool_call>
<tool_call>
<function=write_file>
<parameter=path>/workspace/results/2.tex</parameter>
<parameter=content>ungrounded table</parameter>
</function>
</tool_call>
'''
    source = {
        'id': 'mixed', 'type': 'function', 'function': {
            'name': 'write_file',
            'arguments': json.dumps(first) + trailing,
        },
    }

    recovered, count = expand_concatenated_write_calls([source])

    assert count == 1
    assert len(recovered) == 1
    assert recovered[0]['id'] == 'mixed_0'
    assert json.loads(recovered[0]['function']['arguments']) == first


def test_malformed_writer_handoff_uses_descendant_file_not_required_directory():
    from src.clean_agent_preview import malformed_write_handoff_target

    assert malformed_write_handoff_target(
        '{"path":"/workspace/results/1.tex"}{"content":"body"}',
        ['/workspace/results'],
    ) == '/workspace/results/1.tex'
    assert malformed_write_handoff_target(
        '{"path":"/workspace/outside.tex"}{"content":"body"}',
        ['/workspace/results'],
    ) == ''


@pytest.mark.asyncio
async def test_binary_artifact_completion_requests_python_not_text_writer(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'make-image',
            'function': {'name': 'python', 'arguments': json.dumps({
                'code': "open('/workspace/output.png', 'wb').write(b'png')",
            })},
        }]}}]},
        {'choices': [{'delta': {'content': 'Created and verified the image.'}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        return block.tool_type, {'output': 'created', 'exit_code': 0}

    monkeypatch.setattr(module, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 0)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] in {'python', 'write_file'}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='kimi-k3',
        messages=[{'role': 'user', 'content': 'Create the requested image.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {
                'required_artifacts': ['/workspace/output.png'],
            },
        }, max_tokens=8192, max_rounds=3,
    )]

    assert executed == ['python']
    assert [
        schema['function']['name'] for schema in requests[0]['tools']
    ] == ['python']
    # Kimi's thinking API does not accept named tool_choice, so provider
    # compatibility enforces the same choice by leaving only Python offered.
    assert 'tool_choice' not in requests[0]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(
        event.get('type') == 'agent_step'
        and event.get('offered_tools') == ['python']
        for event in events
    )


@pytest.mark.asyncio
async def test_action_promise_resumes_tools_then_forces_final_synthesis(monkeypatch):
    import src.clean_agent_preview as module

    call = lambda index: {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': f'inspect-{index}',
            'function': {'name': 'inspect_media', 'arguments': json.dumps({
                'path': '/workspace/input/video.mp4',
                'query': f'segment {index}',
            })},
    }]}}]}
    responses = iter([
        call(1),
        {'choices': [{'delta': {'content': 'Let me inspect the remaining segment.'}}]},
        call(2),
        {'choices': [{'delta': {'content': 'I will now review the frames.'}}]},
        {'choices': [{'delta': {'content': 'Alex served: 6, Sam served: 6.'}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        return block.tool_type, {'output': 'timestamped visual evidence', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'inspect_media'
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='kimi-k3',
        messages=[{'role': 'user', 'content': 'Inspect the full video and report counts.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
        }, max_tokens=8192, max_rounds=6,
    )]

    assert executed == ['inspect_media', 'inspect_media']
    assert requests[2].get('tools')
    assert 'tools' not in requests[4]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    recoveries = [
        event for event in events
        if event.get('reason') == 'action_promise_without_result'
    ]
    assert [event['attempt'] for event in recoveries] == [1, 2]
    assert any(
        event.get('type') == 'final_response'
        and 'Alex served: 6' in event.get('content', '')
        for event in events
    )


@pytest.mark.asyncio
async def test_repeated_off_contract_calls_recover_via_required_artifact_body(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'reasoning_content': 'reasoning-one', 'tool_calls': [{
            'index': 0, 'id': 'bad-bash',
            'function': {'name': 'bash', 'arguments': json.dumps({
                'command': 'echo nope',
            })},
        }]}}]},
        {'choices': [{'delta': {'reasoning_content': 'reasoning-two', 'tool_calls': [{
            'index': 0, 'id': 'bad-python',
            'function': {'name': 'python', 'arguments': json.dumps({
                'code': 'print("nope")',
            })},
        }, {
            'index': 1, 'id': 'bad-inspect-sibling',
            'function': {'name': 'inspect_media', 'arguments': json.dumps({
                'path': '/workspace/input.png',
            })},
        }]}}]},
        {'choices': [{'delta': {'content': ''}}]},
        {'choices': [{'delta': {'content': '<html><body>Recovered</body></html>'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block)
        return block.tool_type, {'output': 'written', 'exit_code': 0}

    monkeypatch.setattr(module, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 0)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'write_file'
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='deepseek-flash',
        messages=[{'role': 'user', 'content': 'Create the requested HTML.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {
                'required_artifacts': ['/workspace/output.html'],
            },
        }, max_tokens=8192, max_rounds=5,
    )]

    assert [block.tool_type for block in executed] == ['write_file']
    prior_tool_turn = next(
        message for message in requests[1]['messages']
        if message.get('role') == 'assistant' and message.get('tool_calls')
    )
    assert prior_tool_turn['reasoning_content'] == 'reasoning-one'
    assert requests[2]['max_tokens'] == 8192
    assert requests[3]['max_tokens'] == 8192
    assert executed[0].content == (
        '/workspace/output.html\n<html><body>Recovered</body></html>'
    )
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert sum(
        event.get('type') == 'tool_output'
        and event.get('execution_attempted') is False
        for event in events
    ) == 3
    assert any(
        event.get('type') == 'artifact_body_handoff'
        and event.get('path') == '/workspace/output.html'
        for event in events
    )


def test_runtime_required_artifacts_does_not_promote_inputs_to_outputs():
    assert runtime_required_artifacts(
        'Read /workspace/input/data.json and write /workspace/results/report.json.',
        {'completion_requirements': {'required_artifacts': ['/workspace/results/report.json']}},
    ) == ('/workspace/results/report.json',)


def test_prompt_input_paths_are_not_misclassified_as_required_artifacts():
    assert runtime_required_artifacts(
        'Use read_file to read /workspace/sample.txt. Read only.', {}
    ) == ()
    assert runtime_required_artifacts(
        'Use OCR to extract text from /workspace/receipt.png. Read only.', {}
    ) == ()


def test_prompt_output_path_is_tracked_without_its_source_path():
    assert runtime_required_artifacts(
        'Create an HTML report at /workspace/output.html from /workspace/input.csv.',
        {},
    ) == ('/workspace/output.html',)


def test_empty_runner_artifact_list_falls_back_to_prompt_output_path():
    assert runtime_required_artifacts(
        'Create an HTML report at /workspace/output.html from /workspace/input.csv.',
        {'completion_requirements': {'required_artifacts': []}},
    ) == ('/workspace/output.html',)


def test_preferences_are_inputs_not_required_outputs_for_a_saved_digest():
    from src.clean_agent_preview import declared_workspace_artifacts

    assert declared_workspace_artifacts(
        'Local preferences live at /workspace/config/interests.json and '
        '/workspace/config/categories.json; they contain input data. '
        'Save everything to /workspace/results/paper_digest.md.'
    ) == ('/workspace/results/paper_digest.md',)


def test_compact_writer_advertises_parallel_independent_file_calls():
    writer = next(
        schema for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if schema['function']['name'] == 'write_file'
    )
    assert 'multiple write_file calls in the same response' in writer['function']['description']


def test_read_only_python_does_not_satisfy_required_artifact():
    required = ('/workspace/output.html',)
    assert not execution_targets_required_artifact(
        'python', {'code': "Image.open('/workspace/input/reference.png')"}, required,
    )
    assert execution_targets_required_artifact(
        'python', {'code': "open('/workspace/output.html', 'w').write(body)"}, required,
    )
    assert execution_targets_required_artifact(
        'write_file', {'path': '/workspace/output.html', 'content': '<html />'}, required,
    )
    assert not execution_targets_required_artifact(
        'write_file', {'path': '/workspace/analyze.py', 'content': 'print(1)'}, required,
    )


def test_read_only_gate_blocks_mutation_and_network_shell():
    assert readonly_call('manage_notes', {'action': 'list'})
    assert readonly_call('manage_notes', {'action': 'view', 'id': 'abc'})
    assert not readonly_call('manage_notes', {'action': 'delete', 'id': 'abc'})
    assert not readonly_call('bash', {'command': 'curl https://example.com'})
    assert not readonly_call('mcp__email__send_email', {})


def test_preview_allows_safe_personal_writes_only():
    assert preview_call_allowed('manage_notes', {'action': 'add', 'title': 'x'}, 'add a note')
    assert preview_call_allowed('manage_tasks', {'action': 'create', 'task': 'x'}, 'add a task')
    assert preview_call_allowed('manage_calendar', {'action': 'create_event', 'summary': 'x'}, 'add a calendar event')
    assert preview_call_allowed('manage_memory', {'action': 'edit', 'id': 'x'}, 'edit my memory')
    assert preview_call_allowed('create_document', {'title': 'x'}, 'make a document')
    assert preview_call_allowed('manage_notes', {'action': 'delete', 'id': 'x'}, 'delete a note')
    assert not preview_call_allowed('manage_tasks', {'action': 'run', 'id': 'x'}, 'run task')
    assert not preview_call_allowed('send_email', {'to': 'x@example.com'}, 'send email')
    assert not preview_call_allowed('bash', {'command': 'true'}, 'run shell')


def test_preview_allows_read_only_email_screening_tools():
    assert preview_call_allowed(
        'scan_spam', {'account': 'Primary Inbox', 'folder': 'INBOX'},
        'anything junky in the first inbox?',
    )
    assert preview_call_allowed(
        'scan_email_unsubscribes', {'account': 'Primary Inbox'},
        'scan newsletters for unsubscribe links',
    )


def test_explicit_primary_inbox_is_preserved_when_email_search_omits_account():
    from src.clean_agent_preview import preserve_requested_email_account

    assert preserve_requested_email_account(
        'mcp__email__search_emails', {'query': 'vendor constraints'},
        user_text='Search my primary inbox for the latest constraints',
    ) == {'query': 'vendor constraints', 'account': 'Primary Inbox'}
    assert preserve_requested_email_account(
        'mcp__email__search_emails', {'query': 'vendor constraints'},
        user_text='Search all my mailboxes',
    ) == {'query': 'vendor constraints'}


@pytest.mark.parametrize("tool,args,prompt", [
    ('send_email', {'to': 'x@example.com', 'subject': 'Hi', 'body': 'Ready'}, 'send email to x@example.com'),
    ('reply_to_email', {'uid': '1001', 'body': 'Ready'}, 'reply now to UID 1001'),
    ('send_to_session', {'session_id': 'chat-1', 'message': 'Ready'}, 'send the chat this message'),
    ('chat_with_model', {'model': 'provider/model', 'message': 'Question'}, 'ask provider/model this question'),
    ('pipeline', {'steps': [{'model': 'provider/model', 'prompt': 'Question'}]}, 'run a model pipeline'),
])
def test_contract_required_external_operations_are_preview_safe(tool, args, prompt):
    decision = evaluate_preview_call(
        tool, args, prompt,
        turn_authorized_families={'email', 'sessions'},
        contract_required_tools={tool},
    )
    assert decision.allowed, decision.audit()


def test_external_operations_remain_blocked_without_exact_contract_requirement():
    decision = evaluate_preview_call(
        'send_email',
        {'to': 'x@example.com', 'subject': 'Hi', 'body': 'Ready'},
        'send email to x@example.com',
        turn_authorized_families={'email'},
    )
    assert not decision.allowed


@pytest.mark.parametrize("tool,args", [
    ('manage_research', {'action': 'list'}),
    ('manage_research', {'action': 'read', 'id': 'report-1'}),
    ('list_sessions', {}),
    ('manage_contact', {'action': 'list'}),
    ('manage_contact', {'action': 'search', 'query': 'Alex'}),
])
def test_supplemental_private_inventory_reads_are_preview_safe(tool, args):
    decision = evaluate_preview_call(tool, args, 'list my saved data')
    assert decision.allowed and decision.reason == 'allowed'


@pytest.mark.parametrize("tool,args", [
    ('manage_research', {'action': 'delete', 'id': 'report-1'}),
    ('manage_contact', {'action': 'add', 'name': 'Alex', 'email': 'a@example.com'}),
])
def test_supplemental_private_inventory_mutations_remain_blocked(tool, args):
    decision = evaluate_preview_call(tool, args, 'list my saved data')
    assert not decision.allowed


@pytest.mark.parametrize('sentinel', ['', 'all', 'all sessions', 'all_sessions', 'no_filter', '*'])
def test_preview_unfiltered_session_sentinels_do_not_become_literal_title_filters(sentinel):
    tool, args = normalize_preview_function_args('list_sessions', {'filter': sentinel})
    assert tool == 'list_sessions'
    assert args == {}


def test_preview_preserves_real_session_title_filter():
    tool, args = normalize_preview_function_args('list_sessions', {'filter': 'audit'})
    assert tool == 'list_sessions'
    assert args == {'filter': 'audit'}


def test_plain_note_list_drops_model_invented_search_and_type_filters():
    tool, args = normalize_preview_function_args(
        'manage_notes',
        {
            'action': 'list', 'title': 'Top Three Notes', 'note_type': 'note',
            'pinned': True,
        },
        user_text='list those again, top three only',
    )
    assert tool == 'manage_notes'
    assert args == {'action': 'list'}


def test_grounded_note_list_filters_are_preserved():
    tool, args = normalize_preview_function_args(
        'manage_notes',
        {'action': 'list', 'query': 'packing', 'pinned': True, 'archived': True},
        user_text='list archived pinned notes matching packing',
    )
    assert tool == 'manage_notes'
    assert args == {
        'action': 'list', 'query': 'packing', 'pinned': True, 'archived': True,
    }


def test_skill_walkthrough_normalizes_invented_reference_to_skill_body_view():
    tool, args = normalize_preview_function_args(
        'manage_skills',
        {
            'action': 'view_ref', 'name': 'action-evidence-synthesis',
            'path': 'references/details.md',
        },
        user_text='what does the first one actually do? walk me thru it',
    )
    assert tool == 'manage_skills'
    assert args == {'action': 'view', 'name': 'action-evidence-synthesis'}


@pytest.mark.parametrize('message', [
    'Which one runs most often?',
    'do those show next run time too or only status?',
    'how often does that task execute?',
])
def test_task_questions_over_list_evidence_require_synthesis(message):
    assert task_list_requires_synthesis(message)


def test_plain_task_inventory_keeps_canonical_renderer():
    assert not task_list_requires_synthesis('list my first three tasks and statuses')


def test_empty_note_search_blocks_referential_view_of_older_list_item():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'search-1', 'function': {
                'name': 'manage_notes',
                'arguments': json.dumps({'action': 'search', 'query': 'apartment lease'}),
            },
        }]},
        {'role': 'tool', 'tool_call_id': 'search-1', 'content': json.dumps({
            'response': 'No notes found.', 'exit_code': 0,
        })},
    ]
    assert note_search_result_empty(history[-1]['content'])
    assert note_referent_error(
        'manage_notes', {'action': 'view', 'id': 'older-note'},
        user_text='open that one', history=history,
    )
    assert note_referent_error(
        'manage_notes', {'action': 'view', 'id': 'explicit-note'},
        user_text='open note explicit-note', history=history,
    ) is None


def test_server_sealed_read_forces_exact_first_tool_then_releases_choice():
    from src.turn_contract import RequiredReadOperation, resolve_turn_contract

    contract = resolve_turn_contract(
        capabilities={'contacts'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(),
        required_read_operation=RequiredReadOperation(
            'manage_contact', {'action': 'list'}, max_items=3,
        ),
    )
    offered = compact_schemas(contract.schemas())
    assert required_read_tool_choice(contract, offered) == {
        'type': 'function', 'function': {'name': 'manage_contact'},
    }
    assert required_read_tool_choice(contract, offered, calls=1) is None
    assert sealed_read_arguments(
        contract, 'manage_contact', {'action': 'delete', 'id': 'wrong'}
    ) == {'action': 'list'}
    assert sealed_read_arguments(
        contract, 'manage_contact', {'action': 'delete'}, calls=1
    ) == {'action': 'delete'}


def test_server_sealed_document_list_enforces_contract_limit():
    from src.turn_contract import RequiredReadOperation, resolve_turn_contract

    contract = resolve_turn_contract(
        capabilities={'documents'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(),
        required_read_operation=RequiredReadOperation(
            'manage_documents', {'action': 'list'}, max_items=3,
        ),
    )
    assert sealed_read_arguments(
        contract, 'manage_documents', {'action': 'list'}
    ) == {'action': 'list', 'limit': 3}


def test_server_sealed_calendar_read_preserves_model_resolved_range():
    from src.turn_contract import RequiredReadOperation, resolve_turn_contract

    contract = resolve_turn_contract(
        capabilities={'calendar'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(),
        required_read_operation=RequiredReadOperation(
            'manage_calendar', {'action': 'list_events'}, max_items=3,
        ),
    )
    assert sealed_read_arguments(
        contract,
        'manage_calendar',
        {
            'action': 'list_events',
            'start': '2026-09-12T00:00:00',
            'end': '2026-09-13T00:00:00',
            'summary': 'unsafe mutation field',
        },
    ) == {
        'action': 'list_events',
        'start': '2026-09-12T00:00:00',
        'end': '2026-09-13T00:00:00',
    }


def test_email_identifier_guard_rejects_placeholder_after_failed_listing():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'call-list', 'function': {'name': 'list_emails', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'call-list', 'content': json.dumps({
            'exit_code': 1, 'error': 'email backend unavailable',
        })},
    ]
    error = email_identifier_error(
        'mcp__email__read_email', {'message_id': '<msg-id>'}, history=history,
    )
    assert error and 'placeholders are not executable' in error


def test_email_identifier_guard_accepts_successful_result_user_id_and_active_email():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'call-list', 'function': {'name': 'list_emails', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'call-list', 'content': 'Subject: Hello\nUID: 8492'},
        {'role': 'system', 'content': 'Active email context\nMessage UID: open-44'},
    ]
    assert email_identifier_error('read_email', {'uid': '8492'}, history=history) is None
    assert email_identifier_error('draft_email_reply', {'uid': 'open-44'}, history=history) is None
    assert email_identifier_error(
        'read_email', {'uid': 'user-77'}, user_text='read email UID user-77', history=history,
    ) is None
    assert email_identifier_error('read_email', {'uid': 'invented'}, history=history)


def test_email_identifier_guard_decodes_mcp_stdout_before_extracting_uid():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'call-search', 'function': {
                'name': 'mcp__email__search_emails', 'arguments': '{}',
            },
        }]},
        {'role': 'tool', 'tool_call_id': 'call-search', 'content': json.dumps({
            'stdout': (
                'Found 1 email(s):\nUID: 104\n'
                'Account: Research Mail <alex.research@rowan.studio>'
            ),
            'stderr': '', 'exit_code': 0,
        })},
    ]

    assert email_identifier_error(
        'mcp__email__draft_email_reply', {'uid': '104'}, history=history,
    ) is None


def test_email_identifier_guard_accepts_uid_nested_in_successful_stdout_result():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'call-search',
            'function': {'name': 'mcp__email__search_emails', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'call-search', 'content': json.dumps({
            'stdout': 'Found 1 email\nUID: 104\nAccount: Research Mail',
            'stderr': '',
            'exit_code': 0,
        })},
    ]

    assert email_identifier_error(
        'mcp__email__draft_email_reply', {'uid': '104'}, history=history,
    ) is None


def test_canonical_result_renderers_honor_explicit_user_count_limits():
    assert requested_item_limit('return at most three titles', default=20) == 3
    assert requested_item_limit('whats on my notes list? three at most, dont touch anything', default=20) == 3
    assert requested_item_limit('show only 2', default=20) == 2
    assert requested_item_limit('pull them up, just 3 short ones', default=20) == 3
    assert requested_item_limit('list my notes please, 3 titles max', default=20) == 3
    assert requested_item_limit('list those again, three only', default=20) == 3
    assert requested_item_limit('maybe first three titles', default=20) == 3
    assert requested_item_limit('keep it to three titles', default=20) == 3
    assert requested_item_limit('top 3 titles', default=20) == 3
    assert requested_item_limit('list those again, 3', default=20) == 3
    assert requested_item_limit('three names and their statuses max', default=20) == 3
    assert requested_item_limit('just titles, 3 max, read only pls', default=20) == 3
    assert requested_item_limit(
        'what tasks do i have scheduled? 3 is fine, need name + status', default=20,
    ) == 3
    assert requested_item_limit(
        'can you check my calendar and give me the next 3 events? titles and times only',
        default=20,
    ) == 3
    assert requested_item_limit('show me my saved memories, only a few', default=20) == 3
    assert requested_item_limit("what's in my memory? just a few", default=20) == 3
    assert requested_item_limit(
        'Can you show my notes? I only need three titles. Read-only, keep it short.',
        default=20,
    ) == 3
    assert requested_item_limit('what notes have i got? 3 titles tops', default=20) == 3
    assert requested_item_limit(
        'list my memorries, three short ones, read-only please', default=20,
    ) == 3
    assert requested_item_limit('list those again, three tops', default=20) == 3
    assert requested_item_limit('same again but cap it at three, read only', default=20) == 3
    assert requested_item_limit('what tasks are set up? no more than 3 names', default=20) == 3
    assert requested_item_limit('i only want 3 names and statuses', default=20) == 3
    assert requested_item_limit(
        "only need the first three names + whether theyre running or paused", default=20,
    ) == 3
    assert requested_item_limit('three short bits, dont change anything', default=20) == 3
    assert requested_item_limit(
        'what do you remember about me? show me like three things max', default=20,
    ) == 3
    assert requested_item_limit(
        'what scheduled tasks do i have? three names + status max', default=20,
    ) == 3
    assert requested_item_limit(
        'can u list my calendar? three titles and times max, no edits', default=20,
    ) == 3
    notes = '- [a] **One**\n- [b] **Two**\n- [c] **Three**\n- [d] **Four**'
    rendered_notes = notes_terminal_response(notes, user_text='List at most three titles')
    assert 'Four' not in rendered_notes
    assert '<!-- ody-more-notes:' not in rendered_notes
    assert rendered_notes.count('#note-') == 3
    calendar = (
        'Found 4 event(s):\n'
        '- 2026-09-11T09:00:00 -> 2026-09-11T10:00:00: A\n'
        '- 2026-09-12T09:00:00 -> 2026-09-12T10:00:00: B\n'
        '- 2026-09-13T09:00:00 -> 2026-09-13T10:00:00: C\n'
        '- 2026-09-14T09:00:00 -> 2026-09-14T10:00:00: D'
    )
    rendered_calendar = calendar_terminal_response(
        calendar, user_text='Return at most three titles and times',
    )
    assert '\n- D' not in rendered_calendar


def test_calendar_terminal_response_recovers_truncated_json_envelope():
    raw = (
        '{"response": "Found 3 event(s):\\n'
        '- 2026-09-11T09:00:00 -> 2026-09-11T10:00:00: '
        '[One](#event-one)\\n'
        '- 2026-09-12T09:00:00 -> 2026-09-12T10:00:00: '
        '[Two](#event-two)\\n'
        '- 2026-09-13T09:00:00 -> 2026-09-13T10:00:00: '
        '[Three](#event-three)'
    )

    rendered = calendar_terminal_response(raw, user_text='show my calendar')

    assert rendered.startswith('I found 3 calendar events in that range:')
    assert '[One](#event-one)' in rendered
    assert '[Two](#event-two)' in rendered
    assert not rendered.startswith('{"response"')
    tasks = json.dumps({'response': (
        'Found 4 tasks:\n'
        '1. One (1) — active, daily, 09:00\n'
        '2. Two (2) — paused, daily, 10:00\n'
        '3. Three (3) — active, daily, 11:00\n'
        '4. Four (4) — active, daily, 12:00'
    )})
    rendered_tasks = tasks_terminal_response(
        tasks,
        user_text="only need the first three names + whether they're running or paused",
    )
    assert '[One]' in rendered_tasks and '[Two]' in rendered_tasks and '[Three]' in rendered_tasks
    assert '[Four]' not in rendered_tasks
    memories = (
        'Found 4 memory entries:\n'
        '- [fact] `a` — One\n- [fact] `b` — Two\n'
        '- [fact] `c` — Three\n- [fact] `d` — Four'
    )
    rendered_memories = memory_terminal_response(
        json.dumps({'results': memories}), user_text='List at most three',
    )
    assert '#memory-d' not in rendered_memories
    servers = (
        '4 configured server(s) (default: Ajax):\n'
        '- One → host1\n- Two → host2\n- Three → host3\n- Four → host4'
    )
    rendered_servers = cookbook_servers_terminal_response(
        servers, user_text='List those again, at most three',
    )
    assert 'Four →' not in rendered_servers
    assert '...and 1 more' in rendered_servers
    tasks = (
        'Found 4 tasks:\n'
        '1. One (id1) — active, daily\n2. Two (id2) — paused, cron\n'
        '3. Three (id3) — active\n4. Four (id4) — active'
    )
    rendered_tasks = tasks_terminal_response(
        json.dumps({'response': tasks}), user_text='Just names and status, three max.',
    )
    assert '#task-id1' in rendered_tasks
    assert '— active' in rendered_tasks
    assert 'Four' not in rendered_tasks


def test_structured_list_history_matches_the_rows_shown_to_the_user():
    history = [
        {'role': 'assistant', 'tool_calls': [{'id': 'call-1'}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'all 30 raw rows'},
    ]
    visible = 'Here are your notes (30):\n- One\n- Two\n- Three'

    align_structured_tool_history(history, visible)

    assert history[-1]['content'] == visible


def test_memory_tool_result_stays_row_parseable_before_observation_cap():
    from src.clean_agent_preview import preview_tool_result_text

    rows = 'Found 323 memory entries:\n\n' + '\n'.join(
        f'- [fact] `id-{index}` — Memory {index}' for index in range(400)
    )
    output = preview_tool_result_text(
        {'results': rows, 'exit_code': 0}, 'manage_memory', {'action': 'list'},
    )
    assert output.startswith('Found 323 memory entries:')
    assert '- [fact] `id-0` — Memory 0' in output
    assert not output.startswith('{')


def test_calendar_referential_repeat_inherits_successful_scope_but_new_period_wins():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'calendar-1', 'function': {
                'name': 'manage_calendar',
                'arguments': json.dumps({
                    'action': 'list_events',
                    'start': '2026-09-14T00:00:00',
                    'end': '2026-09-21T00:00:00',
                }),
            },
        }]},
        {'role': 'tool', 'tool_call_id': 'calendar-1', 'content': json.dumps({
            'response': 'Found 1 event', 'exit_code': 0,
        })},
    ]
    repeated = inherit_referential_read_arguments(
        'manage_calendar', {'action': 'list_events'},
        user_text='List those again, at most three.', history=history,
    )
    assert repeated['start'] == '2026-09-14T00:00:00'
    assert repeated['end'] == '2026-09-21T00:00:00'
    shifted = inherit_referential_read_arguments(
        'manage_calendar', {'action': 'list_events'},
        user_text='Show those next week instead.', history=history,
    )
    assert 'start' not in shifted and 'end' not in shifted


def test_calendar_pure_repeat_discards_model_invented_filter():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'calendar-1', 'function': {
                'name': 'manage_calendar',
                'arguments': '{"action":"list_events"}',
            },
        }]},
        {'role': 'tool', 'tool_call_id': 'calendar-1', 'content': json.dumps({
            'response': 'Found three events', 'exit_code': 0,
        })},
    ]
    assert inherit_referential_read_arguments(
        'manage_calendar',
        {'action': 'list_events', 'query': 'Marzia', 'start': '2026-09-12T00:00:00Z'},
        user_text="List those again, three max. Don't change anything.",
        history=history,
    ) == {'action': 'list_events'}


def test_search_recovery_groups_cosmetic_rewrites_and_extracts_requested_source():
    assert normalized_search_intent('The official IANA example domains page') == 'iana example domains'
    assert normalized_search_intent('IANA example domains') == 'iana example domains'
    assert requested_web_source_links('Return one official source link')
    assert requested_web_source_links(
        'look up GPT-4 online, one official link. just reading, terse'
    )
    assert web_source_links('[1] Example Domains\n    https://www.iana.org/help/example-domains') == [
        ('https://www.iana.org/help/example-domains',
         '[Source: Example Domains](https://www.iana.org/help/example-domains)')
    ]
    sources = (
        '[1] GPT-4 - Wikipedia\n    https://en.wikipedia.org/wiki/GPT-4\n'
        '[2] GPT-4 | OpenAI\n    https://openai.com/index/gpt-4/'
    )
    assert web_source_links(sources, prefer_official=True, query='GPT-4')[0][0] == 'https://openai.com/index/gpt-4/'
    assert web_source_links(
        '[1] GPT-4 facts and release date\n    https://www.xda-developers.com/gpt-4-facts',
        prefer_official=True, query='GPT-4 official source',
    ) == []
    assert requested_web_link_limit('Return one official source link') == 1
    assert requested_web_link_limit(
        'look up GPT-4 online, one official link. just reading, terse'
    ) == 1
    assert web_source_links(
        '[1] OWASP Juice Shop\n    https://owasp.org/www-project-juice-shop/',
        prefer_official=True,
        query='official Python packaging guide',
    ) == []
    iana = (
        '[1] IANA-managed Reserved Domains\n    https://www.iana.org/domains/reserved\n'
        '[2] Example Domains\n    https://www.iana.org/help/example-domains'
    )
    assert web_source_links(iana, query='IANA example domains page')[0][0].endswith('/help/example-domains')
    recent = preserve_requested_web_recency(
        'web_search', {'query': 'quantum physics'},
        user_text='Any latest info on quantum physics',
    )
    assert recent['query'].startswith('quantum physics latest 20')
    assert preserve_requested_web_recency(
        'web_search', {'query': 'quantum physics recent news'},
        user_text='latest quantum physics news',
    )['query'] == 'quantum physics recent news'
    assert preserve_requested_web_recency(
        'web_search', {'query': 'GPT-4'}, user_text='Find one official source for GPT-4',
    )['query'] == 'GPT-4 official source site:openai.com'
    assert preserve_requested_web_recency(
        'web_search', {'query': 'official Python packaging guide'},
        user_text='Find the official Python packaging guide',
    )['query'].endswith('site:packaging.python.org')


def test_single_required_operation_is_forced_without_sealed_arguments():
    contract = SimpleNamespace(
        required={'manage_calendar'}, required_read_operation=None,
        permits=lambda name: name == 'manage_calendar',
    )
    offered = [{'function': {'name': 'manage_calendar'}}]
    assert required_read_tool_choice(contract, offered) == {
        'type': 'function', 'function': {'name': 'manage_calendar'},
    }
    write_contract = SimpleNamespace(
        required={'manage_notes'}, required_read_operation=None,
        permits=lambda name: name == 'manage_notes',
    )
    assert required_read_tool_choice(
        write_contract, [{'function': {'name': 'manage_notes'}}],
    ) == {'type': 'function', 'function': {'name': 'manage_notes'}}
    multi = SimpleNamespace(
        required={'manage_calendar', 'search_emails'}, required_read_operation=None,
        permits=lambda _name: True,
    )
    offered = [
        {'function': {'name': 'manage_calendar'}},
        {'function': {'name': 'mcp__email__search_emails'}},
    ]
    assert required_read_tool_choice(multi, offered) == 'required'
    assert required_read_tool_choice(
        multi, offered, calls=1, attempted_required_tools={'manage_calendar'},
    ) == {'type': 'function', 'function': {'name': 'mcp__email__search_emails'}}


def test_contract_item_limit_exposes_inherited_read_cap():
    contract = SimpleNamespace(required_read_operation=SimpleNamespace(max_items=3))
    assert contract_item_limit(contract, 20) == 3
    assert contract_item_limit(SimpleNamespace(required_read_operation=None), 20) == 20


def test_bounded_research_policy_preserves_tools_before_second_search():
    schemas = [
        {'type': 'function', 'function': {'name': name, 'parameters': {}}}
        for name in ('web_search', 'web_fetch', 'manage_calendar')
    ]
    offered, choice, active = bounded_research_tool_policy(
        schemas, searches=1, retrievals=0,
    )
    assert offered == schemas
    assert choice is None
    assert active is False


def test_bounded_research_policy_forces_fetch_after_two_searches():
    schemas = [
        {'type': 'function', 'function': {'name': name, 'parameters': {}}}
        for name in ('web_search', 'web_fetch', 'manage_calendar')
    ]
    offered, choice, active = bounded_research_tool_policy(
        schemas, searches=2, retrievals=0,
    )
    assert [schema['function']['name'] for schema in offered] == [
        'web_fetch', 'manage_calendar',
    ]
    assert choice == {
        'type': 'function', 'function': {'name': 'web_fetch'},
    }
    assert active is True


def test_narrow_lookup_policy_forces_retrieval_after_one_successful_search():
    schemas = [
        {'type': 'function', 'function': {'name': name, 'parameters': {}}}
        for name in ('web_search', 'web_fetch', 'private_browser')
    ]
    offered, choice, active = bounded_research_tool_policy(
        schemas, searches=1, retrievals=0, search_limit=1,
    )

    assert [schema['function']['name'] for schema in offered] == [
        'web_fetch', 'private_browser',
    ]
    assert choice == {'type': 'function', 'function': {'name': 'web_fetch'}}
    assert active is True


def test_bounded_research_policy_keeps_source_inspection_after_retrieval():
    schemas = [
        {'type': 'function', 'function': {'name': name, 'parameters': {}}}
        for name in ('web_search', 'web_fetch', 'manage_calendar')
    ]
    offered, choice, active = bounded_research_tool_policy(
        schemas, searches=2, retrievals=1,
    )
    assert [schema['function']['name'] for schema in offered] == ['web_fetch', 'manage_calendar']
    assert choice is None
    assert active is True


def test_retrieved_source_urls_accepts_native_lists_and_serialized_arrays():
    assert retrieved_source_urls({
        'urls': ['https://one.example/a', 'https://two.example/b'],
    }) == ['https://one.example/a', 'https://two.example/b']
    assert retrieved_source_urls({
        'urls': '[https://one.example/a, https://two.example/b]',
    }) == ['https://one.example/a', 'https://two.example/b']
    assert retrieved_source_urls({'url': 'file:///tmp/not-public'}) == []


@pytest.mark.asyncio
async def test_stream_bounds_research_to_two_searches_fetch_then_synthesis(monkeypatch):
    import src.clean_agent_preview as module

    def call(index, name, arguments):
        return {'index': index, 'id': f'call-{index}', 'function': {
            'name': name, 'arguments': json.dumps(arguments),
        }}

    packets = iter([
        {'choices': [{'delta': {'tool_calls': [call(0, 'web_search', {'query': 'topic overview'})]}}]},
        {'choices': [{'delta': {'tool_calls': [call(1, 'web_search', {'query': 'topic official source'})]}}]},
        {'choices': [{'delta': {'tool_calls': [call(2, 'web_fetch', {'url': 'https://example.org/source'})]}}]},
        {'choices': [{'delta': {'content': 'Complete evidence-grounded answer with https://example.org/source. ' +
            'The sources describe the topic and explain how the findings were obtained. '
            'Their methods support the reported observations but do not establish every broader claim. '
            'The official source supplies the definitions needed to interpret the comparison. '
            'Independent coverage adds context while also noting the limitations of the available evidence. '
            'These limitations matter when applying the findings to a different setting. '
            'The conclusion should therefore stay within the conditions actually examined, and any '
            'unanswered questions should be checked against additional primary evidence.'}}]},
    ])
    requests = []
    executions = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(packets))

    async def execute(block, **kwargs):
        executions.append(block.tool_type)
        if block.tool_type == 'web_search':
            return 'web_search', {
                'output': '[1] Source\n    https://example.org/source',
                'exit_code': 0,
                'evidence_status': 'available',
            }
        return 'web_fetch', {'output': 'Authoritative source evidence', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] in {'web_search', 'web_fetch'}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Research this topic using authoritative sources.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=5,
    )]

    assert executions == ['web_search', 'web_search', 'web_fetch']
    assert requests[2]['tool_choice'] == {
        'type': 'function', 'function': {'name': 'web_fetch'},
    }
    assert all(
        schema['function']['name'] != 'web_search'
        for schema in requests[2]['tools']
    )
    assert [schema['function']['name'] for schema in requests[3]['tools']] == ['web_fetch']
    assert requests[3].get('tool_choice') != 'none'
    assert any(
        'Retrieved source URLs: https://example.org/source' in str(message.get('content', ''))
        for message in requests[3]['messages']
    )
    assert any('Complete evidence-grounded answer' in chunk for chunk in raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('embedded_article', [False, True])
@pytest.mark.parametrize('empty_second_search', [False, True])
@pytest.mark.parametrize('sources_requested', [False, True])
async def test_stream_research_prerequisite_precedes_broad_web_answer(monkeypatch, embedded_article, empty_second_search, sources_requested):
    """Broad current research expands, retrieves evidence, then synthesizes."""
    import src.clean_agent_preview as module

    packets = [
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'search-1', 'function': {
                'name': 'web_search',
                'arguments': json.dumps({'query': 'latest AI news'}),
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'search-2', 'function': {
                'name': 'web_search',
                'arguments': json.dumps({'query': 'AI policy and model releases today'}),
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'fetch-1', 'function': {
                'name': 'web_fetch',
                'arguments': json.dumps({'url': 'https://example.org/ai-news'}),
            },
        }]}}]},
        {'choices': [{'delta': {'content': (
            'Here is a fuller evidence-based briefing. Recent developments include '
            'new model releases, updated deployment commitments, and policy proposals '
            'from several governments. The first source explains what changed in the '
            'models and how developers can access them. A second independent report '
            'adds context about evaluation, safety, and likely industry effects. The '
            'policy coverage distinguishes proposals from rules already in force and '
            'identifies the dates involved. Taken together, the evidence suggests '
            'continued rapid deployment alongside stronger demands for transparency, '
            'although several announced measures remain preliminary. Readers should '
            'check the linked primary material because this is a changing story. '
            'Sources: https://example.org/ai-news and https://example.org/ai-policy'
        )}}]},
    ]
    article = packets[-1]['choices'][0]['delta']['content']
    if embedded_article or empty_second_search:
        packets.pop(2)
    packets = iter(packets)
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(packets))

    search_calls = 0

    async def execute(block, **kwargs):
        nonlocal search_calls
        if block.tool_type == 'web_search':
            search_calls += 1
            if empty_second_search and search_calls == 2:
                return block.tool_type, {'output': 'No matching results', 'exit_code': 0, 'evidence_status': 'empty'}
        return block.tool_type, {
            'output': '[1] AI News\n    https://example.org/ai-news' + (
                '\n[CONTENT 1] From: https://example.org/ai-news\nTitle: Report\n-----\n'
                + article if embedded_article else ''
            ),
            'exit_code': 0,
            'evidence_status': 'available',
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        s for s in FUNCTION_TOOL_SCHEMAS
        if s['function']['name'] in {'web_search', 'web_fetch'}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Latest news in AI?' + (' Include source links.' if sources_requested else '')}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=5,
    )]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]

    assert len(requests) == (3 if embedded_article or empty_second_search else 4)
    assert requests[1]['tool_choice'] == 'required'
    assert [s['function']['name'] for s in requests[1]['tools']] == ['web_search']
    if not embedded_article and not empty_second_search:
        assert requests[2]['tool_choice'] == {
            'type': 'function', 'function': {'name': 'web_fetch'},
        }
    assert any(s['function']['name'] == 'web_fetch' for s in requests[-1]['tools'])
    assert requests[-1].get('tool_choice') != 'none'
    assert any(
        event.get('type') == 'completion_recovery'
        and event.get('reason') == 'research_before_synthesis'
        for event in events
    )
    assert sum(event.get('reason') == 'research_before_synthesis' for event in events) == 1
    phase_index = next(i for i, event in enumerate(events) if event.get('reason') == 'research_before_synthesis')
    assert not any(event.get('delta') for event in events[:phase_index])
    assert any(
        event.get('type') == 'final_response'
        and 'fuller evidence-based briefing' in event.get('content', '')
        for event in events
    )
    assert not any(
        event.get('delta') == 'Current AI news includes reports about U.'
        for event in events
    )
    # Live drafts may be visible, but the final canonical answer replaces the
    # incomplete draft rather than persisting both as one answer.
    final = [event['content'] for event in events if event.get('type') == 'final_response'][-1]
    assert 'Current AI news includes reports about U.' not in final
    replacement = next(event for event in events if event.get('type') == 'final_response')
    assert replacement['replacement_scope'] == 'turn'
    assert replacement['render_owner'] == 'streamed'
    assert any(event.get('delta') for event in events)


def test_task_renderer_honors_few_and_filters_confirmed_morning_schedule():
    from src.clean_agent_preview import tasks_terminal_response
    raw = {"response": "Found 3 tasks:\n"
           "1. Nightly Audit (audit1) — active, daily, 02:00, next 2026-09-12T02:00:00Z\n"
           "2. Lunch Sync (lunch1) — active, daily, 12:30, next 2026-09-12T12:30:00Z\n"
           "3. Unknown Schedule (unknown1) — active, cron, next 2026-09-12T08:00:00Z"}
    few = tasks_terminal_response(raw, user_text="Just list a few names and status")
    assert few.count("#task-") == 3
    assert "— active" in tasks_terminal_response(
        raw, user_text="Just list a few names and whether they're active",
    )
    morning = tasks_terminal_response(raw, user_text="which run in the morning?")
    assert "Nightly Audit" in morning
    assert "02:00" in morning
    assert "Lunch Sync" not in morning
    assert "Unknown Schedule" not in morning


def test_task_renderer_filters_requested_paused_state_truthfully():
    raw = {"response": "Found 2 tasks:\n"
           "1. Active Job (one) — active, daily\n"
           "2. Paused Job (two) — paused, weekly"}
    rendered = tasks_terminal_response(raw, user_text="which is paused?")
    assert "Paused Job" in rendered
    assert "Active Job" not in rendered
    assert tasks_terminal_response(
        {"response": "Found 1 tasks:\n1. Active Job (one) — active, daily"},
        user_text="which of those is paused?",
    ) == "None of the returned tasks are paused."


def test_skill_renderer_applies_one_global_limit_across_status_groups():
    raw = "## Published\n- **one** (dev): First\n- **two** (agent): Second\n" \
          "## Drafts\n- **three** (general): Third\n- **four**: Fourth"
    rendered = skills_terminal_response(raw, user_text="List those again, at most three")
    assert rendered.count("\n- ") == 4  # three rows plus one overflow row
    assert "one" in rendered and "two" in rendered and "three" in rendered
    assert "four" not in rendered
    assert "[one](#skill-one)" in rendered
    assert "[three](#skill-three)" in rendered


def test_skill_renderer_reports_search_hits_from_structured_result():
    raw = {
        "results": "**artifact-completion**: Create requested artifacts early\n"
                   "  When: Use for persistent deliverables.\n\n"
                   "**reviewable-external-draft**: Prepare an accurate external draft\n"
                   "  When: Use for client-facing updates."
    }
    rendered = skills_terminal_response(raw, user_text="search my skills for email workflow")
    assert rendered.startswith("Skill matches (2):")
    assert "artifact-completion" in rendered
    assert "reviewable-external-draft" in rendered
    assert "[artifact-completion](#skill-artifact-completion)" in rendered
    assert "no saved skill lookup" not in rendered


def test_document_renderer_honors_first_few_and_preserves_links():
    raw = {"response": "Found 4 documents:\n"
           "- [One](#document-one) — markdown\n"
           "- [Two](#document-two) — markdown\n"
           "- [Three](#document-three) — markdown\n"
           "- [Four](#document-four) — markdown"}
    rendered = documents_terminal_response(raw, user_text="first few titles")
    assert rendered.count("#document-") == 3
    assert "Four" not in rendered


def test_shell_listing_renderer_uses_successful_stdout_rows():
    rendered = shell_listing_terminal_response(
        {"output": "alpha\nbeta\ngamma"}, user_text="list whats in there, just names"
    )
    assert rendered == "Workspace items (3):\n- alpha\n- beta\n- gamma"


def test_shell_listing_renderer_does_not_mistake_requested_answer_list_for_files():
    assert shell_listing_terminal_response(
        "f_001.png\nf_002.png\n2",
        user_text="Inspect the video and list every chess move with timestamps.",
    ) == ""


def test_raw_shell_stdout_only_owns_explicit_shell_requests():
    assert direct_shell_output_request("Run this command and return its stdout: uname -a")
    assert direct_shell_output_request("What is the current working directory?")
    assert not direct_shell_output_request(
        "Inspect the poster, calculate the package price, and explain ambiguities."
    )


def test_workspace_path_followup_reuses_prior_pwd_evidence():
    history = [
        {'role': 'tool', 'content': '/workspace'},
        {'role': 'assistant', 'content': 'The current working directory is /workspace.'},
    ]
    assert prior_workspace_path_answer(
        "ok and whats the workspace folder im in?", history
    ) == "The workspace folder is `/workspace`."


def test_hostnamectl_command_gets_portable_hostname_fallback():
    _, args = normalize_preview_function_args(
        'bash', {'command': 'echo MARKER && hostnamectl --static'},
        user_text='print the marker and hostname',
    )
    assert args['command'] == 'echo MARKER && (hostnamectl --static 2>/dev/null || hostname)'


def test_shell_output_renderer_preserves_actual_pwd_result():
    assert shell_output_terminal_response('/workspace') == '/workspace'
    assert shell_output_terminal_response({'output': 'MARKER\nhost-1\n'}) == 'MARKER\nhost-1'


def test_shell_output_renderer_does_not_expose_empty_output_sentinel():
    assert shell_output_terminal_response('(no output)') == ''
    assert shell_output_terminal_response({'output': '(no output)'}) == ''


def test_exact_shell_repeat_inherits_the_prior_command_even_after_failure():
    history = [{
        'role': 'assistant',
        'tool_calls': [{
            'id': 'call-1',
            'function': {
                'name': 'bash',
                'arguments': '{"command":"echo marker && hostnamectl"}',
            },
        }],
    }, {
        'role': 'tool', 'tool_call_id': 'call-1',
        'content': '{"exit_code":1,"output":"marker"}',
    }]
    assert inherit_referential_read_arguments(
        'bash', {'command': 'echo marker && hostname'},
        user_text='run that same command again and give me its actual output',
        history=history,
    ) == {'command': 'echo marker && hostnamectl'}


def test_exact_shell_repeat_ignores_current_model_proposal_already_in_history():
    history = [{
        'role': 'assistant', 'tool_calls': [{
            'id': 'prior', 'function': {
                'name': 'bash', 'arguments': '{"command":"echo marker && hostnamectl"}',
            },
        }],
    }, {
        'role': 'tool', 'tool_call_id': 'prior',
        'content': '{"exit_code":1,"output":"marker"}',
    }, {
        'role': 'assistant', 'tool_calls': [{
            'id': 'current', 'function': {
                'name': 'bash', 'arguments': '{"command":"echo marker && hostname"}',
            },
        }],
    }]
    assert inherit_referential_read_arguments(
        'bash', {'command': 'echo marker && hostname'},
        user_text='run that same command again and give me its actual output',
        history=history,
    ) == {'command': 'echo marker && hostnamectl'}


def test_prior_web_source_answer_uses_latest_successful_web_tool_evidence():
    history = [{
        'role': 'assistant', 'tool_calls': [{
            'id': 'web-1', 'function': {'name': 'web_search', 'arguments': '{}'},
        }],
    }, {
        'role': 'tool', 'tool_call_id': 'web-1',
        'content': '[1] OpenAI GPT-4\n    https://openai.com/index/gpt-4/',
    }]
    assert prior_web_source_answer(
        'where did you get that from, give me the link', history,
    ) == '[Source: OpenAI GPT-4](https://openai.com/index/gpt-4/)'


def test_no_tool_summary_reuses_preceding_short_result_wording():
    history = [{"role": "assistant", "content": "The chair is 91 cm wide."}]
    assert prior_short_answer_for_no_tool_summary(
        "Summarize your preceding result in one sentence. Do not use any tools.",
        history,
    ) == "The chair is 91 cm wide."


def test_no_tool_summary_does_not_replay_a_multiline_digest_verbatim():
    history = [{"role": "assistant", "content": "- Story one\n- Story two"}]
    assert prior_short_answer_for_no_tool_summary(
        "Summarize that in one line, no tools", history,
    ) == ""


def test_empty_research_search_cannot_open_an_unrelated_older_report():
    history = [{
        'role': 'assistant',
        'tool_calls': [{
            'id': 'research-1',
            'function': {
                'name': 'manage_research',
                'arguments': '{"action":"list","search":"battery tech"}',
            },
        }],
    }, {
        'role': 'tool', 'tool_call_id': 'research-1',
        'content': 'No research found in the library. (search: battery tech)',
    }]
    assert research_referent_error(
        'manage_research', {'action': 'open', 'id': 'unrelated'},
        user_text='open that one', history=history,
    )


def test_ui_panel_renderer_does_not_claim_unconfirmed_draft_state():
    assert ui_panel_terminal_response(
        {'ui_event': 'open_panel'}, args={'action': 'open_panel', 'name': 'email'}
    ) == 'Email panel is open.'
    assert ui_panel_terminal_response(
        {
            'ui_event': 'open_panel', 'panel': 'cookbook',
            'view': 'Search', 'view_label': 'models',
        },
        args={'action': 'open_panel', 'name': 'models'},
    ) == 'Cookbook models view is open.'
    assert ui_panel_terminal_response(
        {'ui_event': 'set_theme', 'theme_name': 'dark'},
        args={'action': 'set_theme', 'name': 'dark'},
    ) == 'Dark theme is active.'
    assert ui_panel_terminal_response(
        {'ui_event': 'create_theme', 'theme_name': 'dusk'},
        args={'action': 'create_theme', 'name': 'dusk'},
    ) == 'Dusk theme was created and applied.'
    assert ui_panel_terminal_response(
        {'theme_known': True, 'current_theme': 'dark'},
        args={'action': 'get_theme'},
    ) == 'Current theme: dark.'
    toggle_result = ui_toggle_state_result({
        'web_ui_state': {'web': True, 'bash': False, 'rag': True},
    })
    assert ui_panel_terminal_response(
        toggle_result, args={'action': 'get_toggles'},
    ) == 'Current toggles:\n- web: on\n- bash: off\n- rag: on'
    assert not ui_panel_terminal_response(
        {'results': "Theme changed to 'dark'"},
        args={'action': 'set_theme', 'name': 'dark'},
    )


def test_cookbook_renderer_does_not_invent_live_health_from_configuration():
    rendered = cookbook_servers_terminal_response(
        "2 configured server(s):\n- Ajax → local\n- Odysseus → host",
        user_text="names and status",
    )
    assert "Live online/offline health is not included" in rendered


def test_contentless_final_response_only_matches_empty_answer_announcements():
    from src.clean_agent_preview import contentless_final_response
    assert contentless_final_response("Here is a concise summary of the requested information.")
    assert contentless_final_response("Here's the answer.")
    assert not contentless_final_response("The page is an example domain used in documentation.")


def test_explicit_no_tool_recap_can_reuse_immediately_prior_short_answer():
    from src.clean_agent_preview import prior_short_answer_for_no_tool_summary
    history = [
        {'role': 'assistant', 'content': 'The heading is Example Domain.'},
        {'role': 'user', 'content': 'summarize what you just found in one sentence. no tools.'},
    ]
    assert prior_short_answer_for_no_tool_summary(history[-1]['content'], history) == (
        'The heading is Example Domain.'
    )
    assert prior_short_answer_for_no_tool_summary('tell me more', history) == ''


def test_collection_repeat_is_rendered_from_prior_typed_evidence_with_new_limit():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'notes-1',
            'function': {'name': 'manage_notes', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'notes-1', 'content': json.dumps({'results': (
            '- [n1] **Alpha**\n- [n2] **Beta**\n- [n3] **Gamma**\n- [n4] **Delta**'
        )})},
    ]
    rendered = prior_collection_repeat_answer('just three titles like before', history)
    assert '[Alpha](#note-n1)' in rendered
    assert '[Gamma](#note-n3)' in rendered
    assert '[Delta](#note-n4)' not in rendered
    assert prior_collection_repeat_answer('open the second one', history) == ''


def test_collection_display_reformat_uses_prior_typed_rows_and_limit():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'notes-1',
            'function': {'name': 'manage_notes', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'notes-1', 'content': json.dumps({'results': (
            '- [n1] **Alpha**\n- [n2] **Beta**\n- [n3] **Gamma**\n- [n4] **Delta**'
        )})},
    ]
    rendered = prior_collection_repeat_answer(
        'just titles, 3 max, read only pls', history,
    )
    assert '[Alpha](#note-n1)' in rendered
    assert '[Gamma](#note-n3)' in rendered
    assert '[Delta](#note-n4)' not in rendered


def test_collection_display_reformat_accepts_canonical_note_icons():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'notes-1',
            'function': {'name': 'manage_notes', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'notes-1', 'content': (
            'Here are your notes (4):\n'
            '📝 [Alpha](#note-n1)\n'
            '☑️ [Beta](#note-n2)\n'
            '📝 [Gamma](#note-n3)\n'
            '📝 [Delta](#note-n4)'
        )},
    ]
    rendered = prior_collection_repeat_answer(
        'just titles, 3 max, read only pls', history,
    )
    assert '[Alpha](#note-n1)' in rendered
    assert '[Gamma](#note-n3)' in rendered
    assert '[Delta](#note-n4)' not in rendered


def test_collection_repeat_ignores_negated_change_clause():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'calendar-1',
            'function': {'name': 'manage_calendar', 'arguments': '{"action":"list_events"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'calendar-1', 'content': (
            'I found 3 calendar events in that range:\n'
            '- [Alpha](#event-e1) — Sep 12\n'
            '- [Beta](#event-e2) — Sep 13\n'
            '- [Gamma](#event-e3) — Sep 14'
        )},
    ]
    rendered = prior_collection_repeat_answer(
        "List those same three again. Don't change or send anything.", history,
    )
    assert '[Alpha](#event-e1)' in rendered
    assert '[Gamma](#event-e3)' in rendered


def test_collection_repeat_does_not_preserve_model_invented_memory_filter():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'memory-1',
            'function': {'name': 'manage_memory', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'memory-1', 'content': json.dumps({'results': (
            'Found 3 memory entries:\n\n'
            '- [preference] `aaaa1111` — First memory.\n'
            '- [fact] `bbbb2222` — Second memory.\n'
            '- [project] `cccc3333` — Third memory.'
        )})},
    ]
    rendered = prior_collection_repeat_answer('those agian, max two', history)
    assert 'First memory.' in rendered
    assert 'Second memory.' in rendered
    assert 'Third memory.' not in rendered


def test_collection_repeat_preserves_already_canonical_rows_and_ignores_semantic_followup():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'memory-1',
            'function': {'name': 'manage_memory', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'memory-1', 'content': (
            'Memory: 3 saved entries.\n'
            '- [preference aaaa1111](#memory-aaaa1111) — First memory.\n'
            '- [fact bbbb2222](#memory-bbbb2222) — Second memory.\n'
            '- [project cccc3333](#memory-cccc3333) — Third memory.'
        )},
    ]
    rendered = prior_collection_repeat_answer('those agian, max three', history)
    assert rendered.count('#memory-') == 3
    assert prior_collection_repeat_answer('is one of them about travel? name it', history) == ''


def test_collection_repeat_handles_agen_typo_without_inventing_a_filter():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'notes-1',
            'function': {'name': 'manage_notes', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'notes-1', 'content': json.dumps({'results': (
            '- [n1] **Alpha**\n- [n2] **Beta**\n- [n3] **Gamma**'
        )})},
    ]
    rendered = prior_collection_repeat_answer('list those agen, still max 2', history)
    assert '[Alpha](#note-n1)' in rendered
    assert '[Beta](#note-n2)' in rendered
    assert '[Gamma](#note-n3)' not in rendered


def test_session_collection_link_format_followup_replays_canonical_links():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'sessions-1',
            'function': {'name': 'list_sessions', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'sessions-1', 'content': (
            '{"results": "Found 2 session(s), sorted most-recent first:\\n'
            '- **[Alpha](#session-alpha)** (last active just now)\\n'
            '- **[Beta](#session-beta)** (last active yesterday)\n'
            '[Tool result truncated at 8000 characters.]'
        )},
    ]
    rendered = prior_collection_repeat_answer(
        'keep em as links please, i want to click through', history
    )
    assert '[Alpha](#session-alpha)' in rendered
    assert '[Beta](#session-beta)' in rendered


def test_skill_repeat_applies_new_cap_to_json_wrapped_tool_payload():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'skills-1',
            'function': {'name': 'manage_skills', 'arguments': '{"action":"list"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'skills-1', 'content': json.dumps({'results': (
            'Skills (4):\n\n**Published**\n- Alpha (general)\n- Beta (agent)\n'
            '**Drafts**\n- Gamma\n- Delta'
        )})},
    ]
    rendered = prior_collection_repeat_answer('again, cap at three', history)
    assert '- [Alpha](#skill-Alpha) (general)' in rendered
    assert '- [Gamma](#skill-Gamma)' in rendered
    assert '- Delta' not in rendered


def test_cookbook_server_repeat_understands_trim_to_limit():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'servers-1',
            'function': {'name': 'list_cookbook_servers', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'servers-1', 'content': (
            '4 configured server(s):\n- Alpha → local\n- Beta → host-b\n'
            '- Gamma → host-c\n- Delta → host-d'
        )},
    ]
    rendered = prior_collection_repeat_answer(
        'same again but trim it to three, no changes', history,
    )
    assert '- Alpha' in rendered
    assert '- Gamma' in rendered
    assert '- Delta' not in rendered


def test_referential_status_followup_preserves_failed_operation_evidence():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'serve-1',
            'function': {'name': 'serve_preset', 'arguments': '{"name":"SD3.5","dry_run":true}'},
        }, {
            'id': 'list-1',
            'function': {'name': 'list_serve_presets', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'serve-1', 'content': json.dumps({
            'error': "No preset matching 'SD3.5'", 'exit_code': 1,
        })},
        {'role': 'tool', 'tool_call_id': 'list-1', 'content': '2 saved serve presets:\n- Alpha\n- Beta'},
    ]
    answer = prior_failed_operation_answer('what would that launch?', history)
    assert 'serve_preset operation did not succeed' in answer
    assert "No preset matching 'SD3.5'" in answer
    assert prior_failed_operation_answer('try that launch again', history) == ''


def test_later_success_clears_failed_operation_followup():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'serve-1', 'function': {'name': 'serve_preset', 'arguments': '{}'},
        }, {
            'id': 'serve-2', 'function': {'name': 'serve_preset', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'serve-1', 'content': '{"error":"missing name","exit_code":1}'},
        {'role': 'tool', 'tool_call_id': 'serve-2', 'content': '{"results":"started","exit_code":0}'},
    ]
    assert prior_failed_operation_answer('did that work?', history) == ''


def test_cookbook_server_followups_reuse_typed_prior_list_with_limits_and_status_caveat():
    history = [
        {'role': 'assistant', 'tool_calls': [{
            'id': 'call-1', 'function': {'name': 'list_cookbook_servers', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': (
            '4 configured server(s) (default: Ajax):\n'
            '- kierkegaard → local\n- Odysseus → remote\n'
            '- Ajax → remote\n- epictetus → remote'
        )},
    ]
    repeated = prior_cookbook_server_answer('again pls, max three', history)
    assert repeated.count('\n- ') == 4  # three rows plus bounded expansion row
    assert '...and 1 more configured servers.' in repeated
    status = prior_cookbook_server_answer('and r any of them offline?', history)
    assert 'Live online/offline health is not included' in status
    assert prior_cookbook_server_answer('show my notes', history) == ''


def test_conversation_retains_visible_answer_after_saved_native_tool_trace():
    from src.clean_agent_preview import conversation
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'open the page', 'metadata': {}},
        {'role': 'assistant', 'content': 'The heading is Example Domain.', 'metadata': {
            'clean_v3_turn': [
                {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call-1', 'function': {'name': 'private_browser', 'arguments': '{}'},
                }]},
                {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'heading Example Domain'},
            ],
        }},
    ])

    rebuilt = conversation(session, [{'role': 'user', 'content': 'summarize that'}])

    assert rebuilt[-2] == {'role': 'assistant', 'content': 'The heading is Example Domain.'}
    assert rebuilt[-1] == {'role': 'user', 'content': 'summarize that'}


def test_conversation_strips_inline_tool_media_without_dropping_prior_turn():
    from src.clean_agent_preview import conversation
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'inspect the page', 'metadata': {}},
        {'role': 'assistant', 'content': 'The heading is Example Domain.', 'metadata': {
            'clean_v3_turn': [
                {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'heading Example Domain'},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Visual evidence returned by tool execution.'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'x' * 30000}},
                ]},
            ],
        }},
    ])

    rebuilt = conversation(session, [{'role': 'user', 'content': 'summarize that'}])

    assert any(row.get('content') == 'The heading is Example Domain.' for row in rebuilt)
    assert 'base64' not in json.dumps(rebuilt)
    assert any(row.get('content') == 'heading Example Domain' for row in rebuilt)


def test_referenced_link_note_add_is_grounded_from_prior_evidence():
    args = ground_referenced_note_content(
        'manage_notes', {'action': 'add', 'title': 'Reference'},
        user_text='save that link to a note',
        history=[{'role': 'tool', 'content': 'Fetched https://packaging.python.org/ successfully'}],
    )
    assert args['content'] == 'Saved link: https://packaging.python.org/'
    corrected = ground_referenced_note_content(
        'manage_notes', {
            'action': 'add', 'title': 'Reference',
            'content': 'Saved link: https://invented.example/',
        },
        user_text='save that link to a note',
        history=[{'role': 'tool', 'content': 'Fetched https://packaging.python.org/ successfully'}],
    )
    assert corrected['content'] == 'Saved link: https://packaging.python.org/'


def test_calendar_list_moves_filter_and_completes_this_month_bounds():
    tool, args = normalize_preview_function_args(
        'manage_calendar', {'action': 'list_events', 'summary': 'print shop'},
        user_text='check my calendar for print shop dates this month',
    )
    assert tool == 'manage_calendar'
    assert args['query'] == 'print shop'
    assert 'summary' not in args
    assert re.fullmatch(r'\d{4}-\d{2}-01', args['start'])
    assert re.fullmatch(r'\d{4}-\d{2}-(?:28|29|30|31)', args['end'])


def test_private_browser_navigation_outcome_helpers_handle_batches_and_redirects():
    args = {'action': 'batch', 'commands': [
        ['open', 'https://shop.example/chairs'], ['snapshot'],
    ]}
    result = {'output': json.dumps([{
        'command': ['open', 'https://shop.example/chairs'],
        'success': True,
        'result': {'url': 'https://shop.example/storage'},
    }])}
    assert private_browser_open_url(args) == 'https://shop.example/chairs'
    assert private_browser_effective_url(result) == 'https://shop.example/storage'


def test_browser_access_gate_is_not_treated_as_page_evidence():
    from src.clean_agent_preview import browser_observation_access_blocked

    assert browser_observation_access_blocked(
        'Iframe "DataDome CAPTCHA" Access is temporarily restricted'
    )
    assert browser_observation_access_blocked('Checking your browser before continuing')
    assert not browser_observation_access_blocked(
        'Article opening: Markets rose after the policy announcement.'
    )


@pytest.mark.parametrize('title,snapshot,blocked', [
    ('Client Challenge', '(empty page)', True),
    ('Just a moment...', '(empty page)', True),
    ('Example documentation', '(empty page)', False),
    ('Client Challenge', 'An article discussing client challenge design.', False),
])
def test_browser_challenge_title_with_empty_snapshot(title, snapshot, blocked):
    from src.clean_agent_preview import browser_observation_access_blocked
    raw = json.dumps([
        {'command': ['open', 'https://example.org'], 'result': {'title': title}, 'success': True},
        {'command': ['snapshot'], 'result': {'snapshot': snapshot}, 'success': True},
    ])
    assert browser_observation_access_blocked(raw) is blocked


def test_rendered_missing_page_is_not_article_evidence():
    from src.clean_agent_preview import browser_observation_page_missing

    assert browser_observation_page_missing(
        '- heading "Whoops!" [level=1]\n'
        '- paragraph: This page doesn’t exist or can’t be found.'
    )
    assert browser_observation_page_missing('- heading "404 Page not found" [level=1]')
    assert not browser_observation_page_missing(
        '- heading "HTTP error handling" [level=1]\n'
        '- paragraph: A 404 indicates a missing resource.'
    )


def test_search_embedded_article_requires_readable_body_not_source_metadata():
    from src.clean_agent_preview import search_embedded_article_urls
    header = '[CONTENT 1] From: https://example.org/story\nTitle: Report\n------------------------------\n'
    body = (
        'The council published its transport review on Tuesday following a six month study. '
        'Researchers counted journeys at twelve stations and interviewed residents about access. '
        'Their findings showed that evening services were less reliable than morning departures. '
        'Officials proposed additional buses on weekends while retaining existing train schedules. '
        'The proposal will go through public consultation before any funding decision is made. '
        'Several community groups welcomed the announcement but requested detailed cost estimates. '
        'The report includes methodology, regional comparisons, and limitations of the passenger survey. '
        'A further review is scheduled after the consultation closes next month.'
    )
    assert search_embedded_article_urls(header + body) == ['https://example.org/story']
    assert search_embedded_article_urls('[1] Report\n    https://example.org/story') == []
    assert search_embedded_article_urls(header + 'Short snippet.') == []
    assert search_embedded_article_urls(header + 'Verify you are human. ' + body) == []


def test_repeated_navigation_only_fetch_is_not_treated_as_page_evidence():
    navigation = (
        'World SECTIONS Politics Tech TOP STORIES Newsletter Sign In Subscribe '
        'The Morning Wire See All Newsletters Asia Pacific Europe Africa '
    )
    assert web_fetch_observation_is_boilerplate(navigation * 5)
    assert not web_fetch_observation_is_boilerplate(
        'The article reports that four crew members were aboard. ' * 8
    )


@pytest.mark.parametrize('prompt', [
    "What's new in Sweden?",
    'What is new in Japan?',
    'Anything new in AI?',
    'Latest AI news?',
])
def test_broad_current_web_request_covers_natural_phrasings(prompt):
    assert broad_current_web_request(prompt)


def test_broad_briefing_requires_substance_and_clickable_source_links():
    from src.clean_agent_preview import incomplete_broad_web_answer

    substantial = ' '.join(['substantive'] * 90)
    assert incomplete_broad_web_answer(substantial, 'AI news')
    assert not incomplete_broad_web_answer(
        substantial + ' https://example.org/report', 'AI news'
    )
    assert not incomplete_broad_web_answer('Short answer.', 'What is Python?')


@pytest.mark.parametrize('attempts', [1, 2, 3])
def test_broad_briefing_quality_repair_cannot_restart_again(attempts):
    from src.clean_agent_preview import incomplete_broad_web_answer

    short = 'One headline. https://example.org/news'
    assert incomplete_broad_web_answer(short, 'Latest Sweden news?')
    assert not incomplete_broad_web_answer(
        short, 'Latest Sweden news?', recovery_attempts=attempts,
    )


def test_bounded_web_evidence_answer_preserves_sources_without_claiming_synthesis():
    answer = bounded_web_evidence_answer(
        "What's happening in Norway?",
        [
            '[Source: Norway election update](https://example.org/norway-election)',
            '[Source: Norway economy update](https://example.net/norway-economy)',
        ],
    )

    assert 'could not complete a reliable synthesis' in answer
    assert 'https://example.org/norway-election' in answer
    assert 'https://example.net/norway-economy' in answer
    assert 'repeated a tool call after that tool was disabled' not in answer


def test_followup_search_must_change_subject_angle_not_only_freshness():
    from src.clean_agent_preview import repeated_search_refinement

    assert repeated_search_refinement('latest AI news this week', ['ai news'])
    assert repeated_search_refinement('current Sweden events', ['sweden events'])
    assert not repeated_search_refinement(
        'Sweden election coalition negotiations', ['sweden current events']
    )


def test_evidence_tools_reject_only_exact_duplicates_not_distinct_followups():
    from src.clean_agent_preview import evidence_tool_keeps_distinct_requests_available

    assert evidence_tool_keeps_distinct_requests_available('web_fetch')
    assert evidence_tool_keeps_distinct_requests_available('inspect_media')
    assert not evidence_tool_keeps_distinct_requests_available('write_file')


def test_current_search_arguments_repair_stale_year_and_add_freshness():
    args = preserve_requested_web_recency(
        'web_search',
        {'query': 'Norway current events updated 2025'},
        user_text="What's happening in Norway?",
    )

    assert '2025' not in args['query']
    assert str(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).year) in args['query']
    assert args['time_filter'] == 'day'


@pytest.mark.parametrize('missing_query', [{}, {'query': ''}, {'query': '  ', 'time_filter': 'day'}])
@pytest.mark.parametrize('prior_intents', [[], ['ai news']])
def test_missing_refinement_query_is_not_fabricated(missing_query, prior_intents):
    with pytest.raises(ValueError, match='explicit nonempty query'):
        preserve_requested_web_recency(
            'web_search', missing_query,
            user_text='serch latest ai news pls',
            prior_search_intents=prior_intents,
        )


def test_official_manual_does_not_invent_pdf_requirement():
    args = preserve_requested_web_recency(
        'web_search',
        {'query': 'WIKING Miro stove manual official source'},
        user_text='Find the official English WIKING Miro stove manual online.',
    )

    assert 'filetype:pdf' not in args['query']
    explicit = preserve_requested_web_recency(
        'web_search', {'query': 'camera manual'},
        user_text='Find the official camera manual PDF.',
    )
    assert explicit['query'].endswith('filetype:pdf')


def test_unknown_official_domain_cannot_be_proven_by_pdf_suffix():
    raw = '''
[1] WIKING Miro 4 Wood Burning Stove
    https://scottishstovecentre.co.uk/product/wiking-miro-4/
[2] WIKING Miro Installation and User Manual
    https://www.hwam.com/pub/media/wiking/53-0756_Miro_EN.pdf
'''

    links = web_source_links(
        raw, max_items=2, prefer_official=True,
        query='WIKING Miro stove manual official source filetype:pdf',
    )

    assert links == []
    # Candidates remain in the full tool output for the model to inspect.
    assert len(web_source_links(raw, max_items=2, query='WIKING Miro')) == 2


def test_web_fetch_collapses_single_and_batch_url_fields_without_losing_targets():
    tool, args = normalize_preview_function_args('web_fetch', {
        'url': 'https://example.org/a',
        'urls': ['https://example.org/a', 'https://example.org/b'],
    })

    assert tool == 'web_fetch'
    assert args == {
        'urls': ['https://example.org/a', 'https://example.org/b'],
    }


def test_manual_locator_requests_one_result_but_needs_manufacturer_evidence():
    from src.clean_agent_preview import (
        requested_web_link_limit, requested_web_source_links, web_source_links,
    )

    prompt = 'Find the official WIKING Miro 3 English manual online'
    raw = (
        '[1] WIKING Miro manual\nhttps://manuals.plus/wiking-miro\n'
        '[2] WIKING Miro 3 manuals\nhttps://www.manualslib.com/wiking-miro-3\n'
        '[3] WIKING Miro English manual PDF\n'
        'https://www.hwam.com/media/wiking/miro-en.pdf\n'
    )

    assert requested_web_source_links(prompt)
    assert requested_web_link_limit(prompt) == 1
    assert web_source_links(
        raw, max_items=1, prefer_official=True, query=prompt,
    ) == []


def test_preview_allows_only_reversible_client_local_ui_control():
    assert preview_call_allowed(
        'ui_control', {'action': 'open_panel', 'name': 'gallery'}, 'open gallery'
    )
    assert preview_call_allowed(
        'ui_control', {'action': 'open_panel', 'name': 'settings'}, 'open settings'
    )
    assert preview_call_allowed(
        'ui_control', {'action': 'set_theme', 'name': 'dark'}, 'use the dark theme'
    )
    assert preview_call_allowed(
        'ui_control', {
            'action': 'create_theme', 'name': 'dusk',
            'colors': {
                'bg': '#1b2230', 'fg': '#f5e6c8', 'panel': '#242d3d',
                'border': '#4d596b', 'accent': '#e8ad63',
            },
        }, 'make me a custom dusk theme with slate and amber colors'
    )
    assert preview_call_allowed(
        'ui_control', {'action': 'get_theme'}, 'what theme am I using?'
    )
    assert preview_call_allowed(
        'ui_control', {'action': 'switch_model', 'name': 'other'}, 'switch models'
    )
    assert not preview_call_allowed(
        'ui_control', {'action': 'switch_model', 'name': 'other'}, 'open the cookbook'
    )
    assert not preview_call_allowed(
        'ui_control', {'action': 'toggle', 'name': 'web', 'value': 'off'}, 'turn off web'
    )
    assert preview_call_allowed(
        'ui_control', {'action': 'get_toggles'}, 'which toggles are on?'
    )


def test_bash_requires_explicit_turn_enablement():
    args = {'command': "printf '%s\\n' ODY_SHELL_FILES_READONLY; cat /etc/hostname"}
    assert not preview_call_allowed('bash', args, 'run this command')
    assert preview_call_allowed('bash', args, 'run this command', allow_execute_code=True)


def test_native_workspace_tools_require_validated_native_scope():
    samples = {
        'inspect_media': {'path': '/workspace/fixture.webm'},
        'extract_text': {'path': '/workspace/fixture.png'},
        'ls': {'path': '/workspace'},
        'write_file': {'path': '/workspace/output.html', 'content': '<html></html>'},
    }
    for name, args in samples.items():
        assert not preview_call_allowed(
            name, args, 'work in the supplied workspace',
            allow_execute_code=True,
        )
        assert preview_call_allowed(
            name, args, 'work in the supplied workspace',
            allow_execute_code=True,
            allow_native_workspace=True,
        )


def test_compact_native_file_schemas_preserve_argument_semantics():
    schemas = {
        schema['function']['name']: schema['function']
        for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
    }
    read_properties = schemas['read_file']['parameters']['properties']
    write_properties = schemas['write_file']['parameters']['properties']
    assert 'line 4 means offset=4' in read_properties['offset']['description']
    assert 'final newline' in write_properties['content']['description']
    assert '/workspace refers to its root' in schemas['python']['description']


def test_explicit_final_newline_is_preserved_for_exact_file_content():
    tool, args = normalize_preview_function_args(
        'write_file',
        {'path': '/workspace/result.txt', 'content': 'DONE'},
        user_text=(
            'Write /workspace/result.txt containing exactly DONE followed by a newline.'
        ),
    )
    assert tool == 'write_file'
    assert args['content'] == 'DONE\n'


def test_file_content_is_not_changed_without_explicit_newline_request():
    _, args = normalize_preview_function_args(
        'write_file',
        {'path': '/workspace/result.txt', 'content': 'DONE'},
        user_text='Write /workspace/result.txt containing DONE.',
    )
    assert args['content'] == 'DONE'


def test_unrequested_invalid_transcript_precision_is_dropped():
    _, args = normalize_preview_function_args(
        'transcribe_media',
        {'path': '/workspace/audio.wav', 'timestamp_precision': 100},
        user_text='Return timestamped segments.',
    )
    assert args == {'path': '/workspace/audio.wav'}


def test_explicit_transcript_precision_remains_strictly_validated():
    _, args = normalize_preview_function_args(
        'transcribe_media',
        {'path': '/workspace/audio.wav', 'timestamp_precision': 100},
        user_text='Use timestamp precision of 2 decimal places.',
    )
    assert args['timestamp_precision'] == 100


def test_visual_tool_result_is_uniformly_bounded_to_endpoint_limit():
    from src.clean_agent_preview import bounded_visual_result_blocks

    blocks = bounded_visual_result_blocks({
        'images': [
            {'mimeType': 'image/jpeg', 'data': str(index)}
            for index in range(5)
        ],
    }, max_images=3)
    assert [block['image_url']['url'] for block in blocks] == [
        'data:image/jpeg;base64,0',
        'data:image/jpeg;base64,2',
        'data:image/jpeg;base64,4',
    ]


def test_brokered_web_fetch_and_deliberately_offered_private_browser_are_preview_safe_reads():
    assert preview_call_allowed(
        'web_fetch', {'url': 'https://www.reuters.com/example'}, 'tell me more'
    )
    assert preview_call_allowed(
        'private_browser', {'action': 'open', 'url': 'https://example.com'}, 'open this'
    )


def test_preview_policy_decisions_have_stable_sanitized_reasons():
    allowed = evaluate_preview_call('web_fetch', {'url': 'https://example.com'}, 'tell me more')
    assert allowed.allowed and allowed.reason == 'allowed'
    assert allowed.audit() == {
        'allowed': True, 'reason': 'allowed', 'tool': 'web_fetch',
        'family': 'search_browser',
        'effects': ['brokered_network_read', 'network_egress'],
    }
    denied = evaluate_preview_call('manage_notes', {'action': 'delete', 'id': 'x'}, 'delete it')
    assert not denied.allowed and denied.reason == 'write_family_not_authorized'
    assert 'delete it' not in json.dumps(denied.audit())


def test_native_external_tool_requires_declared_runtime_contract():
    native = {
        'allow_native_workspace': True,
        'external_runtime_tools': {'http_request'},
    }
    allowed = evaluate_preview_call(
        'http_request', {'url': 'http://localhost:9110/slack/messages'}, **native,
    )
    assert allowed.allowed
    assert allowed.reason == 'allowed_external_runtime_contract'


def test_external_http_request_requires_native_workspace_and_declared_schema():
    args = {'url': 'http://127.0.0.1:9100/gmail/messages'}
    undeclared = evaluate_preview_call(
        'http_request', args, allow_native_workspace=True,
    )
    interactive = evaluate_preview_call(
        'http_request', args, external_runtime_tools={'http_request'},
    )
    assert not undeclared.allowed
    assert undeclared.reason == 'tool_not_in_model_runtime'
    assert not interactive.allowed
    assert interactive.reason == 'tool_not_in_model_runtime'


def test_explicit_calendar_event_delete_is_allowed_without_weakening_other_deletes():
    allowed = evaluate_preview_call(
        'manage_calendar',
        {'action': 'delete_event', 'event_id': 'event-1'},
        'remove the happy horizon event',
    )
    assert allowed.allowed and allowed.reason == 'allowed'
    assert not preview_call_allowed(
        'manage_calendar',
        {'action': 'delete_event', 'event_id': 'event-1'},
        'do it',
    )
    assert preview_call_allowed(
        'manage_notes', {'action': 'delete', 'id': 'note-1'}, 'delete the note'
    )


def test_explicit_followup_mutation_uses_the_immutable_turn_family():
    args = {'action': 'delete_event', 'event_id': 'event-1'}
    denied = evaluate_preview_call(
        'manage_calendar', args, 'delete the amazon delivery',
    )
    assert not denied.allowed and denied.reason == 'write_family_not_authorized'

    allowed = evaluate_preview_call(
        'manage_calendar', args, 'delete the amazon delivery',
        turn_authorized_families={'calendar'},
    )
    assert allowed.allowed and allowed.reason == 'allowed'


def test_explicit_followup_forget_uses_the_immutable_memory_family():
    allowed = evaluate_preview_call(
        'manage_memory', {'action': 'delete', 'memory_id': 'memory-1'},
        'Forget that memory.', turn_authorized_families={'memory'},
    )
    assert allowed.allowed and allowed.reason == 'allowed'


def test_skill_update_alias_normalizes_to_edit_before_policy():
    tool, args = normalize_preview_function_args(
        'manage_skills', {'action': 'update', 'name': 'example-skill', 'description': 'new'},
    )
    assert tool == 'manage_skills'
    assert args['action'] == 'edit'


def test_private_browser_open_never_creates_an_internal_batch():
    tool, args = normalize_preview_function_args(
        'private_browser',
        {'action': 'open', 'url': 'https://example.com', 'timeout_ms': 12000},
    )

    assert tool == 'private_browser'
    assert args == {
        'action': 'open',
        'url': 'https://example.com',
        'timeout_ms': 12000,
    }


def test_private_browser_local_artifact_open_is_not_rewritten():
    tool, args = normalize_preview_function_args(
        'private_browser',
        {'action': 'open', 'url': 'file:///workspace/output.html'},
    )

    assert tool == 'private_browser'
    assert args == {'action': 'open', 'url': 'file:///workspace/output.html'}


def test_private_browser_dom_batch_only_matches_automatic_open_snapshot():
    assert private_browser_dom_batch({
        'action': 'batch',
        'commands': [['open', 'https://example.com'], ['snapshot']],
    })
    assert not private_browser_dom_batch({
        'action': 'batch',
        'commands': [['open', 'https://example.com'], ['snapshot'], ['screenshot']],
    })


def test_saved_tool_trace_retains_only_latest_browser_screenshot():
    executions = []
    first = {'type': 'tool_output', 'tool': 'private_browser', 'screenshot': 'data:image/png;base64,one'}
    middle = {'type': 'tool_output', 'tool': 'manage_notes'}
    latest = {'type': 'tool_output', 'tool': 'private_browser', 'screenshot': 'data:image/png;base64,two'}
    record_tool_execution(executions, first)
    record_tool_execution(executions, middle)
    record_tool_execution(executions, latest)
    assert 'screenshot' not in executions[0]
    assert executions[1] is middle
    assert executions[2]['screenshot'].endswith('two')


def test_every_compactly_offered_preview_tool_has_valid_policy_permitted_call():
    samples = {
            'ask_user': ({'question': 'Which one?', 'options': [{'label': 'First'}, {'label': 'Second'}]}, 'ask me which one'),
            'python': ({'code': 'print(2 + 2)'}, 'calculate this'),
            'read_file': ({'path': '/workspace/input.txt'}, 'read this file'),
            'app_api': ({
            'action': 'call', 'method': 'GET',
            'path': '/api/hwfit/models?fit_only=true&limit=10&sort=fit',
            }, 'find the best model to run on my hardware'),
            'ask_teacher': ({'problem': 'Check whether this claim is grounded'}, 'ask the teacher model to review this claim'),
        'extract_text': ({'path': 'odysseus://attachment/fixture.png'}, 'OCR this image'),
        'edit_image': ({'image_id': 'owned-image', 'action': 'upscale', 'scale': 2}, 'upscale this image 2x'),
        'generate_image': ({'prompt': 'A city'}, 'Make an image of a city'),
        'bash': ({'command': 'pwd'}, 'run this shell command'),
        'create_document': ({'title': 'x', 'content': 'y'}, 'create a document'),
            'edit_document': ({'edits': [{'find': 'x', 'replace': 'y'}]}, 'edit my document'),
                'draft_email': ({'to': 'a@example.com', 'subject': 'Review', 'body': 'Draft'}, 'draft an email to a@example.com for review'),
                'resolve_contact': ({'name': 'Jon'}, 'Write an email to Jon'),
            'draft_email_reply': ({'uid': '1', 'body': 'Thursday suits better'}, 'draft a reply to email UID 1 for review'),
                'download_attachment': ({'uid': '1', 'index': 0}, 'open attachment 0 on email UID 1'),
                'manage_email_state': ({'action': 'list_blocked'}, 'show my blocked senders list'),
            'download_model': ({
            'repo_id': 'Qwen/Qwen3-8B', 'include': '*.safetensors',
        }, 'download Qwen/Qwen3-8B locally with only *.safetensors files'),
        'list_cached_models': ({}, 'list cached models'),
        'list_cookbook_servers': ({}, 'list cookbook servers'),
        'list_downloads': ({}, 'list downloads'),
        'manage_endpoints': ({'action': 'list'}, 'list configured endpoints'),
        'manage_mcp': ({'action': 'list'}, 'list connected MCP servers'),
        'manage_tokens': ({'action': 'list'}, 'list API token names'),
        'manage_webhooks': ({'action': 'list'}, 'list configured webhooks'),
        'manage_settings': ({'action': 'list_tools'}, 'show the tool toggles'),
        'list_email_accounts': ({}, 'list my email accounts'),
        'list_emails': ({'limit': 3}, 'list my emails'),
        'list_models': ({}, 'list models'),
        'list_serve_presets': ({}, 'list serve presets'),
        'list_served_models': ({}, 'list served models'),
        'serve_preset': ({'name': 'SD3.5'}, 'launch my SD3.5 preset'),
        'stop_served_model': ({'session_id': 'serve-abc12345'}, 'stop that model server'),
        'tail_serve_output': ({'session_id': 'serve-abc12345'}, 'show that model server log'),
            'manage_calendar': ({'action': 'list_events'}, 'list my calendar events'),
            'manage_contact': ({'action': 'list'}, 'list my contacts'),
            'manage_documents': ({'action': 'list'}, 'list my documents'),
            'manage_memory': ({'action': 'list'}, 'list my memories'),
            'manage_notes': ({'action': 'list'}, 'list my notes'),
            'manage_research': ({'action': 'list'}, 'list my saved research reports'),
            'manage_skills': ({'action': 'list'}, 'list my skills'),
            'manage_tasks': ({'action': 'list'}, 'list my tasks'),
            'list_sessions': ({}, 'list my chat sessions'),
        'create_session': ({'name': 'Review', 'model': 'qwen'}, 'create a chat session named Review using qwen'),
        'send_to_session': ({'session_id': 'abc', 'message': 'hello'}, 'send hello to chat session abc'),
        'manage_session': ({'action': 'rename', 'session_id': 'abc', 'value': 'Review'}, 'rename chat session abc to Review'),
        'chat_with_model': ({'model': 'qwen', 'message': 'hello'}, 'ask model qwen to answer hello'),
        'pipeline': ({'steps': [{'model': 'qwen', 'instruction': 'draft'}]}, 'run a model pipeline to draft'),
        'pdf_extract': ({'url': 'https://example.com/x.pdf', 'query': 'metric'}, 'read this pdf'),
        'private_browser': ({'action': 'session_info'}, 'use the private browser'),
        'read_email': ({'uid': '1'}, 'read my email'),
        'reply_to_email': ({'uid': '1', 'body': 'Thanks'}, 'reply to email UID 1 saying Thanks'),
        'search_chats': ({'query': 'project'}, 'search my chats'),
            'search_emails': ({'query': 'project'}, 'search my emails'),
            'scan_spam': ({'account': 'Primary Inbox', 'folder': 'INBOX'}, 'scan my inbox for spam'),
            'scan_email_unsubscribes': ({'account': 'Primary Inbox'}, 'scan my inbox for unsubscribe links'),
            'search_hf_models': ({'query': 'Qwen'}, 'search Hugging Face models'),
        'send_email': ({'to': 'a@example.com', 'subject': 'Status', 'body': 'Ready'}, 'send email to a@example.com subject Status body Ready'),
        'suggest_document': ({'suggestions': [{'find': 'x', 'replace': 'y', 'reason': 'clarity'}]}, 'suggest edits to my document'),
        'update_document': ({'content': 'updated'}, 'update my document'),
        'ui_control': ({'action': 'open_panel', 'name': 'gallery'}, 'open gallery'),
        'trigger_research': ({'topic': 'AI info'}, 'research AI info'),
        'web_fetch': ({'url': 'https://example.com'}, 'read this page'),
        'web_search': ({'query': 'current AI news'}, 'search the web'),
        'youtube_tool': ({'action': 'metadata', 'video_url': 'https://youtube.com/watch?v=x'}, 'read YouTube metadata'),
    }
    schemas = {
        schema['function']['name']: schema
        for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if schema['function']['name'] in samples
    }
    assert set(samples) == set(schemas)
    from src.clean_agent_preview import PREVIEW_TOOLS
    assert set(samples) == set(PREVIEW_TOOLS)
    for name, (args, prompt) in samples.items():
        jsonschema.validate(args, schemas[name]['function']['parameters'])
        decision = evaluate_preview_call(
            name, args, prompt, allow_execute_code=(name in {'bash', 'python'}),
            turn_authorized_families=(
                {'research'} if name == 'trigger_research'
                    else {'email'} if name in {'send_email', 'reply_to_email', 'draft_email', 'draft_email_reply'}
                else {'sessions'} if name in {
                    'create_session', 'send_to_session', 'manage_session',
                    'chat_with_model', 'pipeline',
                }
                    else {'cookbook_admin'} if name in {
                        'app_api', 'ask_teacher', 'download_model', 'serve_preset', 'stop_served_model',
                        'tail_serve_output',
                    }
                else {'image_editing'} if name == 'edit_image'
                else {'image_generation'} if name == 'generate_image'
                else frozenset()
            ),
            contract_required_tools={name},
        )
        assert decision.allowed, f'{name}: {decision.reason}'


def test_full_compact_inventory_contains_pipeline_for_exact_session_contract():
    from src.turn_contract import resolve_full_inventory_contract

    contract = resolve_full_inventory_contract(
        schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy(),
    )
    assert 'pipeline' in contract.offered


def test_pdf_extract_accepts_task_local_path_and_normalizes_it_for_execution():
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'pdf_extract'
    )
    arguments = {
        'path': '/workspace/fixtures/paper.pdf',
        'query': 'references arXiv preprints',
    }
    jsonschema.validate(arguments, schema['function']['parameters'])
    block = function_call_to_tool_block('pdf_extract', json.dumps(arguments))
    assert block is not None
    assert json.loads(block.content) == {
        'url': '/workspace/fixtures/paper.pdf',
        'query': 'references arXiv preprints',
    }


def test_pdf_extract_rejects_a_guessed_filename_with_actionable_discovery():
    from src.tool_schemas import normalized_native_function_argument_error

    error = normalized_native_function_argument_error(
        'pdf_extract', {'url': 'example-paper.pdf', 'query': 'Table 2'}
    )

    assert 'public http(s) PDF URL' in error
    assert 'use web_search' in error
    assert normalized_native_function_argument_error(
        'pdf_extract',
        {'url': 'https://example.com/paper.pdf', 'query': 'Table 2'},
    ) is None
    assert normalized_native_function_argument_error(
        'pdf_extract',
        {'url': '/workspace/fixtures/paper.pdf', 'query': 'Table 2'},
    ) is None


def test_write_authority_prevents_cross_family_substitution():
    assert authorized_write_families('send an emil') == {'email'}
    assert not preview_call_allowed('manage_tasks', {'action': 'create', 'name': 'send mail'}, 'send an email')
    assert preview_call_allowed('manage_notes', {'action': 'add', 'title': 'Sweden'}, 'add todo go to Sweden tomorrow')
    assert not preview_call_allowed('manage_notes', {'action': 'add', 'title': 'x'}, 'do it')


def test_immediate_successful_write_allows_same_family_revision_only():
    turn = [
        {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {
            'name': 'manage_calendar',
            'arguments': '{"action":"create_event","summary":"Meeting with Elon","dtstart":"2026-09-09T10:00:00Z"}',
        }}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"uid":"event-1","exit_code":0}'},
        {'role': 'assistant', 'content': 'Meeting added.'},
    ]
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'add meeting elon at 10am'},
        {'role': 'assistant', 'content': 'Meeting added.', 'metadata': {'clean_v3_turn': turn}},
    ])
    inherited = recent_successful_write_families(session)
    assert inherited == {'calendar'}
    assert preview_call_allowed(
        'manage_calendar', {'action': 'update_event', 'event_id': 'event-1'},
        'no tomorrow 10am and alon', contextual_write_families=inherited,
    )
    assert not preview_call_allowed(
        'manage_calendar', {'action': 'create_event', 'summary': 'another'},
        'do it again', contextual_write_families=inherited,
    )
    assert not preview_call_allowed(
        'manage_notes', {'action': 'update', 'id': 'note-1'},
        'change it', contextual_write_families=inherited,
    )


def test_active_editor_authorizes_revision_but_not_replacement_creation():
    context = frozenset({'documents'})
    assert preview_call_allowed(
        'update_document', {'content': 'Hello'}, 'write the email',
        contextual_write_families=context,
    )
    assert not preview_call_allowed(
        'create_document', {'title': 'Replacement', 'content': 'Hello'},
        'write the email', contextual_write_families=context,
    )


def test_open_email_reply_is_scoped_to_one_whole_draft_writer():
    document = SimpleNamespace(
        title='New Email', language='email',
        current_content='To: a@example.com\nSubject: Hello\n---\nOriginal body',
    )
    assert active_editor_whole_draft_request(document, 'Write reply this email')
    contract = resolve_full_inventory_contract(
        schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy(),
    )
    scoped = scope_active_editor_contract(contract, whole_draft=True)
    offered = {name.removeprefix('mcp__email__') for name in scoped.offered}
    assert 'update_document' in offered
    assert not {'create_document', 'manage_documents', 'edit_document', 'suggest_document'} & offered


def test_active_review_is_scoped_to_suggestions_without_applying_changes():
    document = SimpleNamespace(
        title='Draft', language='markdown', current_content='A wordy sentence.',
    )
    prompt = 'Add another inline suggestion. Do not apply either suggestion.'
    assert active_editor_suggestion_request(document, prompt)
    contract = resolve_full_inventory_contract(
        schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy(),
    )
    scoped = scope_active_editor_contract(contract, suggestion_only=True)
    assert {name.removeprefix('mcp__email__') for name in scoped.offered} == {'suggest_document'}


def test_explicit_active_document_feedback_forces_the_sole_suggestion_channel():
    offered = [schema for schema in FUNCTION_TOOL_SCHEMAS
               if schema['function']['name'] == 'suggest_document']
    assert required_active_editor_tool_choice(
        active_editor_target=True,
        suggestion_target=True,
        whole_draft_target=False,
        offered=offered,
        calls=0,
    ) == {'type': 'function', 'function': {'name': 'suggest_document'}}
    assert required_active_editor_tool_choice(
        active_editor_target=True,
        suggestion_target=True,
        whole_draft_target=False,
        offered=offered,
        calls=1,
    ) is None


def test_direct_active_document_mutation_requires_one_of_the_offered_writers():
    offered = [schema for schema in FUNCTION_TOOL_SCHEMAS
               if schema['function']['name'] in {'edit_document', 'update_document'}]
    assert required_active_editor_tool_choice(
        active_editor_target=True,
        suggestion_target=False,
        whole_draft_target=False,
        offered=offered,
        calls=0,
    ) == 'required'
    assert required_active_editor_tool_choice(
        active_editor_target=True,
        suggestion_target=False,
        whole_draft_target=False,
        offered=offered,
        calls=1,
    ) is None


@pytest.mark.parametrize('prompt', [
    'Can you broaden this planning review?',
    'Expand this usability review.',
    'Deepen the evidence review in this draft.',
    'Lighten the wording in this critique.',
])
def test_revision_of_review_content_is_not_misclassified_as_advice(prompt):
    document = SimpleNamespace(
        title='Draft', language='markdown', current_content='Current content.',
    )
    assert targets_active_editor(document, prompt)
    assert not active_editor_suggestion_request(document, prompt)


@pytest.mark.parametrize('prompt', [
    'Give feedback on the current draft.',
    'Suggest improvements to this document.',
    'Review the open document and leave comments.',
    'Add an inline suggestion without applying it.',
])
def test_explicit_advisory_requests_use_document_suggestions(prompt):
    document = SimpleNamespace(
        title='Draft', language='markdown', current_content='Current content.',
    )
    assert targets_active_editor(document, prompt)
    assert active_editor_suggestion_request(document, prompt)


def test_failed_write_does_not_authorize_contextual_revision():
    session = SimpleNamespace(history=[{'role': 'assistant', 'content': 'Failed.', 'metadata': {
        'clean_v3_turn': [
            {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {
                'name': 'manage_calendar', 'arguments': '{"action":"create_event"}',
            }}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"error":"blocked","exit_code":1}'},
        ],
    }}])
    assert not recent_successful_write_families(session)


def test_denial_never_claims_action_succeeded():
    text = denied_response().casefold()
    assert 'no changes were made' in text
    assert 'deleted' not in text and 'created' not in text and 'sent' not in text


@pytest.mark.parametrize('text', [
    'Delete all my notes.', 'add a calendar event tomorrow', 'remember that I like tea',
    'create a document called Atlas', 'send an email to Alex', 'write the email for me',
])
def test_mutation_request_detection_is_family_aware(text):
    assert requests_mutation(text)


@pytest.mark.parametrize('text', [
    'Show my notes.', 'What is on my calendar?', 'Search the web for Sweden.', 'Explain tasks.',
    'List my notes. Read-only; do not change data or send messages.',
    'List my scheduled tasks. Do not change data or send messages.',
    "Show my notes without changing or deleting anything.",
])
def test_reads_and_general_questions_are_not_mutations(text):
    assert not requests_mutation(text)


def test_definition_note_and_sports_set_are_not_a_notes_mutation():
    text = (
        'How many set points did the player fail to convert? '
        'Note: a set point is one point away from winning the set.'
    )

    assert authorized_write_families(text) == frozenset()
    assert not requests_mutation(text)


def test_plain_note_request_still_authorizes_note_mutation():
    assert authorized_write_families('Set my note title to Travel') == {'notes'}
    assert requests_mutation('Set my note title to Travel')


@pytest.mark.parametrize('prompt', [
    'Block off Tuesday at 2 PM for review.',
    'Reserve Wednesday at 9 AM for planning.',
    'Block Thursday morning for focused work.',
    'Reserve Friday afternoon for Journal Club prep.',
])
def test_calendar_time_block_language_authorizes_calendar_write(prompt):
    assert requests_mutation(prompt)
    decision = evaluate_preview_call(
        'manage_calendar',
        {'action': 'create_event', 'title': 'Focus',
         'start': '2026-09-15T14:00:00', 'end': '2026-09-15T15:00:00'},
        prompt,
        turn_authorized_families={'calendar'},
        contract_required_tools={'manage_calendar'},
    )
    assert decision.allowed, decision.reason


def test_negated_mutation_does_not_hide_later_positive_instruction():
    assert requests_mutation("Don't delete my note; update its title instead.")


def test_completion_claim_distinguishes_success_from_denial_or_question():
    assert claims_completion('All notes have been deleted.')
    assert claims_completion("Done — I've added the note.")
    assert claims_completion(
        'Here is a concise rewrite of the open document, written in a friendly tone.'
    )
    assert not claims_completion("I can't delete those. No changes were made.")
    assert not claims_completion('What title should be added?')


def test_history_preserves_native_call_result_group_and_current_user():
    saved = [{'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {'name': 'manage_notes', 'arguments': '{"action":"list"}'}}]},
             {'role': 'tool', 'tool_call_id': 'c1', 'content': 'notes'},
             {'role': 'assistant', 'content': 'Here are notes.'}]
    session = SimpleNamespace(history=[{'role': 'user', 'content': 'notes'},
                {'role': 'assistant', 'content': 'Here are notes.', 'metadata': {'clean_v3_turn': saved}}])
    result = conversation(session, [{'role': 'user', 'content': 'second one?'}])
    assert result[1:4] == saved
    assert result[-1] == {'role': 'user', 'content': 'second one?'}


def test_history_drops_large_older_turn_without_losing_recent_note_evidence():
    saved = [
        {'role': 'assistant', 'tool_calls': [{'id': 'notes-call', 'function': {
            'name': 'manage_notes', 'arguments': '{"action":"list"}'}}]},
        {'role': 'tool', 'tool_call_id': 'notes-call', 'content': '[fixture-id] Today'},
        {'role': 'assistant', 'content': 'Today'},
    ]
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'Calendar'},
        {'role': 'assistant', 'content': 'x' * 23000},
        {'role': 'user', 'content': 'Notes'},
        {'role': 'assistant', 'content': 'Today', 'metadata': {'clean_v3_turn': saved}},
    ])
    result = conversation(session, [{'role': 'user', 'content': 'Delete that note'}])
    assert result[0]['content'] == 'Notes'
    assert result[1:4] == saved


def test_history_character_limit_drops_whole_oversized_reference_turn():
    # Character-budget limitation: availability of the tool alone does not
    # guarantee that an oversized prior result remains in the model context.
    saved = [
        {'role': 'assistant', 'tool_calls': [{'id': 'large-call', 'function': {
            'name': 'manage_notes', 'arguments': '{"action":"list"}'}}]},
        {'role': 'tool', 'tool_call_id': 'large-call', 'content': 'x' * 23000},
    ]
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'Notes'},
        {'role': 'assistant', 'content': 'Notes', 'metadata': {'clean_v3_turn': saved}},
    ])
    result = conversation(session, [{'role': 'user', 'content': 'Read the second one'}])
    assert result == [{'role': 'user', 'content': 'Read the second one'}]


def test_history_rehydrates_most_recent_owner_checked_image_for_followup(monkeypatch, tmp_path):
    image_path = tmp_path / 'fixture.png'
    image_path.write_bytes(b'PNG-test-bytes')

    class Uploads:
        def resolve_upload(self, upload_id, owner=None, allow_admin=True):
            assert upload_id == 'a' * 32 + '.png'
            assert owner == 'alice'
            assert allow_admin is False
            return {'path': str(image_path), 'mime': 'image/png', 'name': 'fixture.png'}

        def is_image_file(self, name, mime):
            return mime == 'image/png'

    monkeypatch.setattr('src.tool_utils.get_upload_handler', lambda: Uploads())
    upload_id = 'a' * 32 + '.png'
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'What is shown?', 'metadata': {
            'attachments': [{'id': upload_id, 'name': 'fixture.png', 'mime': 'image/png'}],
        }},
        {'role': 'assistant', 'content': 'A test image.', 'metadata': {
            'clean_v3_turn': [{'role': 'assistant', 'content': 'A test image.'}],
        }},
    ])
    result = conversation(session, [{'role': 'user', 'content': 'What color was it?'}], owner='alice')
    image_turn = result[0]
    assert image_turn['role'] == 'user'
    assert image_turn['content'][0] == {'type': 'text', 'text': 'What is shown?'}
    assert image_turn['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert any(block.get('type') == 'text' and f'odysseus://attachment/{upload_id}' in block.get('text', '')
               for block in image_turn['content'])
    assert result[-1] == {'role': 'user', 'content': 'What color was it?'}


def test_history_trims_after_removing_raw_image_bytes(monkeypatch, tmp_path):
    image_path = tmp_path / 'fixture.png'
    image_path.write_bytes(b'PNG-test-bytes')

    class Uploads:
        def resolve_upload(self, upload_id, owner=None, allow_admin=True):
            return {'path': str(image_path), 'mime': 'image/png', 'name': 'fixture.png'}

        def is_image_file(self, name, mime):
            return mime == 'image/png'

    monkeypatch.setattr('src.tool_utils.get_upload_handler', lambda: Uploads())
    upload_id = 'b' * 32 + '.png'
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Inspect this dashboard.'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'x' * 30000}},
        ], 'metadata': {'attachments': [{'id': upload_id}]}},
        {'role': 'assistant', 'content': 'Q3 is highest.'},
        {'role': 'user', 'content': 'Save that in a note.', 'metadata': {}},
    ])
    diagnostics = {}
    result = conversation(
        session, [{'role': 'user', 'content': 'Save that in a note.'}],
        owner='alice', diagnostics=diagnostics,
    )
    assert result[0]['content'][1]['type'] == 'image_url'
    assert diagnostics['image_rehydration'] == 'rehydrated'


def test_history_never_rehydrates_image_without_an_authenticated_owner(monkeypatch):
    monkeypatch.setattr('src.tool_utils.get_upload_handler', lambda: (_ for _ in ()).throw(AssertionError('must not resolve')))
    session = SimpleNamespace(history=[
        {'role': 'user', 'content': 'Image marker', 'metadata': {'attachments': [{'id': 'a' * 32 + '.png'}]}},
        {'role': 'assistant', 'content': 'Seen.'},
    ])
    assert conversation(session, [{'role': 'user', 'content': 'Again?'}])[0]['content'] == 'Image marker'


def test_multimodal_image_count_never_exposes_payloads():
    messages = [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'look'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,secret'}},
        ]},
        {'role': 'assistant', 'content': 'seen'},
    ]
    assert multimodal_image_count(messages) == 1


def test_attachment_reference_count_uses_metadata_only():
    session = SimpleNamespace(history=[
        SimpleNamespace(role='user', content='one', metadata={'attachments': [{'id': 'a'}, {'id': 'b'}]}),
        SimpleNamespace(role='assistant', content='seen', metadata={}),
    ])
    assert attachment_reference_count(session) == 2


def test_plain_system_wrappers_are_not_conversation_messages():
    assert conversation(None, [{'role': 'system', 'content': 'legacy wrapper'}, {'role': 'user', 'content': 'hi'}]) == [{'role': 'user', 'content': 'hi'}]


def test_open_empty_email_draft_is_still_visible_context():
    message = active_document_context_message(SimpleNamespace(
        id='draft-1', title='Reply to Jordan', language='email', current_content='',
    ))
    assert message['role'] == 'user'
    assert message['metadata']['trusted'] is False
    assert 'Open editor kind: email draft' in message['content']
    assert 'Title: Reply to Jordan' in message['content']
    assert 'Content (currently empty)' in message['content']


def test_open_document_context_includes_current_text():
    message = active_document_context_message(SimpleNamespace(
        id='doc-1', title='Trip', language='markdown', current_content='Visit Uppsala.',
    ))
    assert 'Open editor kind: document' in message['content']
    assert 'Visit Uppsala.' in message['content']


def test_active_rich_document_context_replaces_embedded_images():
    message = active_document_context_message(SimpleNamespace(
        id='doc-image',
        title='Illustrated note',
        language='richtext',
        current_content=(
            '<p>Before the image.</p>'
            '<img src="data:image/png;base64,' + ('A' * 10000) + '" alt="Flow chart">'
            '<p>After the image.</p>'
        ),
    ))
    assert '[Image: Flow chart]' in message['content']
    assert 'data:image/png' not in message['content']
    assert 'A' * 1000 not in message['content']
    assert 'Before the image.' in message['content']
    assert 'After the image.' in message['content']


def test_open_email_reader_is_visible_as_typed_untrusted_context():
    message = active_email_context_message({
        'uid': 'fixture-uid', 'folder': 'INBOX', 'account': 'fixture-account',
        'subject': 'Status', 'from': 'Jordan', 'body_preview': 'Can we meet tomorrow?',
    })
    assert message['role'] == 'user'
    assert message['metadata']['trusted'] is False
    assert 'Open email reader' in message['content']
    assert 'Subject: Status' in message['content']
    assert 'Can we meet tomorrow?' in message['content']


def test_active_editor_targeting_distinguishes_edit_from_new_document():
    draft = SimpleNamespace(title='Reply', language='email', current_content='')
    assert targets_active_editor(draft, 'Write reply')
    assert targets_active_editor(draft, 'Draft a reply')
    assert targets_active_editor(draft, "Write a friendly email saying I'll reply tomorrow")
    assert targets_active_editor(draft, 'Make it friendlier')
    assert not targets_active_editor(draft, 'Write a note')
    assert not targets_active_editor(draft, 'Write a JavaScript function')
    assert not targets_active_editor(draft, 'Create a new separate document about Sweden')


def test_inline_suggestion_intent_is_independent_from_document_visibility():
    from src.clean_agent_preview import inline_suggestion_request

    assert inline_suggestion_request('give suggestions to this document')
    assert inline_suggestion_request(
        'Proofread the open document. Create inline suggestions only; do not apply changes.'
    )
    assert inline_suggestion_request(
        'Rewrite the open document to match my writing style. Preserve the meaning and '
        'create inline suggestions only; do not apply changes.'
    )
    assert not inline_suggestion_request('Apply the suggestions to this document')
    assert not inline_suggestion_request(
        'Review the video at /workspace/input/video.mp4 and count every match point.'
    )
    assert not inline_suggestion_request(
        'Review the short handheld clip at /workspace/input/video.mp4 and save an index image.'
    )
    assert not active_editor_suggestion_request(None, 'give suggestions to this document')


def test_ordinary_plural_suggestions_target_the_visible_editor():
    document = SimpleNamespace(
        title='Draft', language='markdown', current_content='Current content.',
    )
    assert targets_active_editor(document, 'give suggestions on this document')
    assert active_editor_suggestion_request(document, 'give suggestions on this document')


def test_later_inline_only_clause_scopes_rewrite_to_suggestions():
    document = SimpleNamespace(
        title='Draft', language='markdown', current_content='Current content.',
    )
    prompt = (
        'Rewrite the open document to match my configured Writing Style setting. '
        'Preserve the meaning and create inline suggestions only; do not apply changes.'
    )
    assert targets_active_editor(document, prompt)
    assert active_editor_suggestion_request(document, prompt)


@pytest.mark.asyncio
async def test_inline_suggestion_without_visible_document_does_not_call_tool(monkeypatch):
    import src.clean_agent_preview as module
    requests = []

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'Ignored'}}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response()

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'manage_documents', 'edit_document', 'update_document', 'suggest_document',
    }]
    contract = replace(
        resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'give suggestions to this document'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), active_document=None,
    )]

    assert 'tools' not in requests[0]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(
        event.get('delta') ==
        'Open the document you want reviewed, then ask for inline suggestions again.'
        for event in events
    )


@pytest.mark.parametrize('prompt', [
    'Make Objective more specific.',
    'Make the opening paragraph clearer.',
    'Make this less formal.',
    'Make it shorter.',
])
def test_make_revision_targets_active_document(prompt):
    document = SimpleNamespace(
        title='Draft', language='markdown', current_content='Current content.',
    )
    assert targets_active_editor(document, prompt)


def test_active_editor_contract_excludes_other_tool_families():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS
               if s['function']['name'] in {
                   'create_document', 'update_document', 'manage_documents',
                   'manage_notes', 'ui_control',
               }]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    scoped = scope_active_editor_contract(contract)
    assert 'create_document' not in scoped.offered
    assert 'ui_control' not in scoped.offered
    assert 'manage_documents' not in scoped.offered
    assert scoped.offered == {'update_document'}
    assert {'create_document', 'ui_control', 'manage_documents'} <= scoped.executable


def test_direct_active_editor_revision_excludes_advisory_suggestion_tool():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'edit_document', 'update_document', 'suggest_document',
    }]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    scoped = scope_active_editor_contract(contract)
    assert scoped.offered == {'edit_document', 'update_document'}


def test_empty_active_editor_contract_offers_only_whole_document_update_from_document_family():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'create_document', 'edit_document', 'suggest_document', 'update_document',
        'manage_documents', 'manage_notes', 'ui_control',
    }]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    scoped = scope_active_editor_contract(contract, empty=True)
    document_tools = {
        name for name in scoped.offered
        if name in {'create_document', 'edit_document', 'suggest_document',
                    'update_document', 'manage_documents'}
    }
    assert document_tools == {'update_document'}
    assert 'manage_notes' not in scoped.offered


def test_active_document_expansion_rejects_meta_placeholder_but_allows_real_prose():
    from src.clean_agent_preview import active_document_revision_quality_error

    document = SimpleNamespace(current_content='An old paragraph.')
    error = active_document_revision_quality_error(
        'update_document',
        {'content': 'An old paragraph.\n\nHere is another paragraph.'},
        active_document=document,
        user_text='expand with another parahraph i meant',
    )
    assert error and 'substantive content' in error
    assert active_document_revision_quality_error(
        'update_document',
        {'content': 'An old paragraph.\n\nThe road curved toward a city glowing at dusk.'},
        active_document=document,
        user_text='add another paragraph',
    ) is None


@pytest.mark.asyncio
async def test_placeholder_document_expansion_is_corrected_before_execution(monkeypatch):
    import src.clean_agent_preview as module

    original = 'An old paragraph.'
    revised = original + '\n\nThe road curved toward a city glowing at dusk.'
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'bad', 'function': {
            'name': 'update_document',
            'arguments': json.dumps({'content': original + '\n\nHere is another paragraph.'}),
        }}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'good', 'function': {
            'name': 'update_document', 'arguments': json.dumps({'content': revised}),
        }}]}}]},
        {'choices': [{'delta': {'content': 'Expanded the document.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block.content)
        return 'update_document', {
            'output': 'Document updated', 'exit_code': 0, 'doc_id': 'draft-1',
            'title': 'Draft', 'language': 'markdown', 'content': block.content,
            'version': 2,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'edit_document', 'update_document', 'suggest_document',
    }]
    contract = replace(
        resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    document = SimpleNamespace(
        id='draft-1', title='Draft', language='markdown', current_content=original,
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'add another paragraph'}], headers={},
        turn_contract=contract, session_id='test', owner='test', disabled_tools=set(),
        tool_policy=ToolPolicy(), active_document=document,
    )]

    assert executed == [revised]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    outputs = [event for event in events if event.get('type') == 'tool_output']
    assert outputs[0]['error'] is True
    assert 'placeholder/meta text' in outputs[0]['output']
    assert outputs[0]['execution_attempted'] is False
    assert outputs[1]['error'] is False


def test_v3_schema_preserves_names_and_action_enum():
    notes = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    compact = compact_schemas([notes])[0]
    assert compact['function']['name'] == 'manage_notes'
    assert compact['function']['parameters']['properties']['action']['enum'] == notes['function']['parameters']['properties']['action']['enum']
    assert compact['function']['description']


@pytest.mark.parametrize('name', ['read_email', 'mcp__email__read_email'])
def test_v3_live_email_schema_preserves_identifier_semantics(name):
    import asyncio
    import copy
    from mcp_servers import email_server

    live = next(tool for tool in asyncio.run(email_server.list_tools()) if tool.name == 'read_email')
    schema = {'type': 'function', 'function': {
        'name': name, 'description': live.description,
        'parameters': copy.deepcopy(live.inputSchema),
    }}
    original = copy.deepcopy(schema)
    function = compact_schemas([schema])[0]['function']
    properties = function['parameters']['properties']

    assert function['name'] == name
    assert schema == original
    assert set(properties) == set(live.inputSchema['properties'])
    assert 'UID' in properties['uid'].get('description', '')
    assert 'search_emails' in properties['uid']['description']
    assert 'within its account and folder' in properties['uid']['description']
    assert 'Folder from the selected result' in properties['folder'].get('description', '')
    assert 'RFC Message-ID header' in properties['message_id'].get('description', '')
    assert 'not a UID' in properties['message_id']['description']
    assert 'uid or message_id' in function['description']


def test_v3_media_schema_preserves_temporal_sampling_semantics():
    media = next(
        schema for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if schema['function']['name'] == 'inspect_media'
    )
    function = media['function']
    properties = function['parameters']['properties']

    assert 'does not locate or count events' in function['description']
    assert 'does not search' in properties['query']['description']
    assert 'overview' in properties['sampling']['description']
    assert 'up to 24' in properties['frames']['description']
    assert properties['frames']['maximum'] == 24
    assert 'midpoint' in properties['segments']['description']


def test_v3_media_overview_defaults_to_three_lossless_sheets():
    import src.clean_agent_preview as module

    tool, arguments = module.normalize_preview_function_args(
        'inspect_media',
        {'path': '/workspace/video.mp4', 'sampling': 'overview'},
    )

    assert tool == 'inspect_media'
    assert arguments['frames'] == 24


def test_v3_transcription_schema_rejects_media_output_semantics_in_prose():
    transcript = next(
        schema for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if schema['function']['name'] == 'transcribe_media'
    )['function']

    assert 'does not inspect pixels or create media clips' in transcript['description']
    assert '.jsonl' in transcript['parameters']['properties']['output_path']['description']


def test_compact_browser_distinguishes_element_refs_from_keyboard_keys():
    original = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'private_browser')
    browser = compact_schemas([original])[0]['function']
    assert 'fill/click/press' not in browser['description']
    assert 'unavailable' in browser['description']
    assert 'commands' not in browser['parameters']['properties']
    assert set(browser['parameters']['properties']) == set(original['function']['parameters']['properties'])


def test_v3_browser_schema_does_not_offer_batch_or_current_tab_authority():
    browser = next(
        schema for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
        if schema['function']['name'] == 'private_browser'
    )['function']
    assert 'commands' not in browser['parameters']['properties']
    assert 'batch' not in browser['parameters']['properties']['action']['enum']
    assert 'unavailable' in browser['description']


def test_v3_browser_target_fields_preserve_selector_semantics():
    browser = next(schema for schema in compact_schemas(FUNCTION_TOOL_SCHEMAS)
                   if schema['function']['name'] == 'private_browser')['function']
    for name in ('target', 'selector'):
        description = browser['parameters']['properties'][name].get('description', '')
        assert '@e2' in description
        assert 'CSS selector' in description
        assert 'not visible text' in description


def test_long_skill_index_retains_readable_names_instead_of_truncated_json():
    from src.clean_agent_preview import preview_tool_result_text
    index = '- **first-skill**: Description\n- **second-skill**: Description\n' + 'Long description ' * 800
    text = preview_tool_result_text({'results': index, 'exit_code': 0}, 'manage_skills', {'action': 'list'})
    assert text.startswith('- **first-skill**: Description\n- **second-skill**: Description\n')
    assert '[Tool result truncated' in text
    # Structured document results retain IDs and other payload fields.
    result = {'results': 'Saved', 'doc_id': 'doc-test'}
    assert json.loads(preview_tool_result_text(result, 'create_document', {})) == result


def test_v3_schema_uses_configured_versioned_contract_root(tmp_path, monkeypatch):
    import src.clean_agent_preview as module

    contract_root = tmp_path / 'contract'
    contract_root.mkdir()
    (contract_root / 'eval_alltools_unseen_compare.py').write_text(
        "def tools_for_mode(tools, mode):\n"
        "    assert mode == 'compact_contract_v5'\n"
        "    tools[0]['function']['description'] = 'versioned-contract-loaded'\n"
        "    return tools\n",
        encoding='utf-8',
    )
    monkeypatch.setenv('ODYSSEUS_TOOL_CONTRACT_ROOT', str(contract_root))
    module.contract_builder.cache_clear()
    try:
        # Probe a tool whose compact description the harness does not
        # replace; manage_notes now carries a full harness-owned override.
        search = next(
            s for s in FUNCTION_TOOL_SCHEMAS
            if s['function']['name'] == 'web_search'
        )
        compact = module.compact_schemas([search])[0]
        assert compact['function']['description'].startswith('versioned-contract-loaded')
    finally:
        module.contract_builder.cache_clear()


def test_v3_document_edit_schema_has_one_unambiguous_structured_form():
    edit = next(s for s in compact_schemas(FUNCTION_TOOL_SCHEMAS)
                if s['function']['name'] == 'edit_document')
    parameters = edit['function']['parameters']
    assert parameters['required'] == ['edits']
    assert set(parameters['properties']) == {'edits', 'more'}
    assert parameters['properties']['more']['type'] == 'boolean'


def test_v3_ui_schema_advertises_only_policy_executable_client_local_actions():
    ui = next(s for s in compact_schemas(FUNCTION_TOOL_SCHEMAS)
              if s['function']['name'] == 'ui_control')
    parameters = ui['function']['parameters']
    assert parameters['required'] == ['action']
    assert parameters['properties']['action']['enum'] == [
        'open_panel', 'set_theme', 'create_theme', 'get_theme', 'get_toggles',
        'switch_model',
    ]
    assert 'enum' not in parameters['properties']['name']
    assert set(parameters['properties']) == {'action', 'name', 'view', 'colors', 'background'}
    assert 'calendar' in parameters['properties']['view']['description']


def test_model_runtime_contains_explicit_tracked_download_tool():
    from src.clean_agent_preview import CONTRACT_REQUIRED_TOOLS, PREVIEW_TOOLS
    assert 'download_model' in CONTRACT_REQUIRED_TOOLS
    assert 'download_model' in PREVIEW_TOOLS


def test_preview_contract_exposes_no_fallback_family_when_required_tool_is_unavailable():
    notes = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    preview = resolve_full_inventory_contract(schemas=[notes], policy=ToolPolicy())
    routed = SimpleNamespace(unavailable=frozenset({'web_search'}), offered=frozenset())
    scoped = scope_preview_contract(preview, routed, {'search_browser'})
    assert scoped.offered == frozenset()
    assert scoped.schemas() == []
    assert scoped.unavailable == {'web_search', 'capability:search_browser'}
    assert scoped.active_capabilities == {'search_browser'}


def test_preview_contract_detects_active_family_removed_by_preview_permissions():
    notes = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    preview = resolve_full_inventory_contract(schemas=[notes], policy=ToolPolicy())
    scoped = scope_preview_contract(
        preview, SimpleNamespace(unavailable=frozenset(), offered=frozenset()), {'search_browser'}
    )
    assert scoped.offered == frozenset()
    assert scoped.unavailable == {'capability:search_browser'}


def test_preview_contract_keeps_only_routed_family_from_trained_inventory():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'manage_notes', 'manage_calendar', 'web_search',
    }]
    preview = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    routed = SimpleNamespace(
        unavailable=frozenset(), offered=frozenset({'manage_notes'}),
        required=frozenset({'manage_notes'}), capabilities=frozenset({'notes'}),
        required_read_operation=None,
    )
    scoped = scope_preview_contract(
        preview, routed, {'notes'}
    )
    assert scoped.offered == {'manage_notes'}
    assert scoped.required == {'manage_notes'}
    assert scoped.capabilities == {'notes'}
    assert {s['function']['name'] for s in scoped.schemas()} == {'manage_notes'}
    assert scoped.active_capabilities == {'notes'}


def test_preview_contract_adds_safe_interactive_core_beside_routed_tools():
    core = {'web_search', 'web_fetch', 'private_browser', 'bash', 'ask_user'}
    schemas = [
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] in core | {'manage_notes'}
    ]
    preview = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    routed = SimpleNamespace(
        unavailable=frozenset(), offered=frozenset({'manage_notes'}),
        required=frozenset({'manage_notes'}), capabilities=frozenset({'notes'}),
        required_read_operation=None,
    )

    scoped = scope_preview_contract(
        preview, routed, {'notes'}, extra_tools=core,
    )

    assert scoped.offered == core | {'manage_notes'}
    assert scoped.required == {'manage_notes'}
    assert {schema['function']['name'] for schema in scoped.schemas()} == core | {'manage_notes'}


def test_preview_contract_keeps_available_family_when_an_independent_family_is_unavailable():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'write_file', 'web_search',
    }]
    preview = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    routed = SimpleNamespace(
        unavailable=frozenset({'manage_calendar'}),
        offered=frozenset({'write_file'}),
        required=frozenset(),
        capabilities=frozenset({'calendar', 'shell_files'}),
        required_read_operation=None,
    )

    scoped = scope_preview_contract(
        preview, routed, {'calendar', 'shell_files'}
    )

    assert scoped.offered == {'write_file'}
    assert {s['function']['name'] for s in scoped.schemas()} == {'write_file'}
    assert scoped.unavailable == {'manage_calendar', 'capability:calendar'}
    assert scoped.active_capabilities == {'calendar', 'shell_files'}


@pytest.mark.asyncio
async def test_stream_emits_incremental_text_and_persistable_history(monkeypatch):
    import src.clean_agent_preview as module
    requests = []
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            for text in ['Hello', ' there']:
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': text}}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            assert kwargs['json']['temperature'] == 0
            assert kwargs['json']['chat_template_kwargs']['enable_thinking'] is False
            return Response()
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(endpoint_url='http://test', model='test', messages=[{'role': 'user', 'content': 'hi'}], headers={}, turn_contract=contract, session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy())]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]
    assert [e['delta'] for e in events if 'delta' in e] == ['Hello', ' there']
    assert requests[0]['messages'][0]['content'].startswith('You are Odysseus.')
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['clean_v3_turn'] == [{'role': 'assistant', 'content': 'Hello there'}]
    assert raw[-1] == 'data: [DONE]\n\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_error', [
    "'Qwen3_5MTPDraftModel' object has no attribute 'language_model'",
    {'message': "'Qwen3_5MTPDraftModel' object has no attribute 'language_model'"},
])
async def test_preview_provider_stream_error_is_terminal_not_empty_answer(monkeypatch, provider_error):
    import src.clean_agent_preview as module

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'error': provider_error})

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'hi'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(),
    )]
    assert raw[-1].startswith('event: error\ndata: ')
    assert 'selected model provider failed' in raw[-1]
    assert 'provider_stream_error' in raw[-1]
    assert 'Qwen3_5MTPDraftModel' not in raw[-1]
    assert all('returned no answer' not in chunk for chunk in raw)
    assert all('"type": "metrics"' not in chunk for chunk in raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [
    (402, 'billing or credits'),
    (401, 'credentials and permissions'),
    (429, 'rate limiting'),
    (503, 'unavailable'),
])
async def test_preview_provider_http_failure_is_terminal_error_not_assistant_text(monkeypatch, status, expected):
    import httpx
    import src.clean_agent_preview as module

    class Response:
        status_code = status

        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        def raise_for_status(self):
            request = httpx.Request('POST', 'https://provider.example/v1/chat/completions')
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError('provider secret must not be shown', request=request, response=response)

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='https://provider.example/v1/chat/completions', model='test',
        messages=[{'role': 'user', 'content': 'hello'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(),
    )]

    assert raw[-1].startswith('event: error\ndata: ')
    assert all('"delta"' not in chunk for chunk in raw)
    assert all('"type": "metrics"' not in chunk for chunk in raw)
    assert 'data: [DONE]' not in raw
    payload = json.loads(raw[-1].split('data: ', 1)[1])
    assert payload['status'] == status
    assert expected in payload['error']
    assert 'provider secret' not in raw[-1]


@pytest.mark.asyncio
async def test_ajax_c375_clean_runtime_uses_progressive_thinking_without_leaking(monkeypatch):
    import src.clean_agent_preview as module
    requests = []

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': '<think>private'}}]})
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': ' trace</think>Hello'}}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response()

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='ajax_c375',
        messages=[{'role': 'user', 'content': 'hi'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), temperature=1.0,
    )]

    assert requests[0]['temperature'] == 0.0
    assert requests[0]['chat_template_kwargs'] == {'enable_thinking': True}
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert [event['delta'] for event in events if 'delta' in event] == ['Hello']
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert metrics['thinking_mode'] == 'progressive_on'


def test_ajax_c375_tool_surface_disables_progressive_thinking():
    from src.clean_agent_preview import progressive_thinking_for_turn

    assert progressive_thinking_for_turn('ajax_c375', [])
    assert not progressive_thinking_for_turn(
        'ajax_c375', [{'function': {'name': 'manage_notes'}}],
    )


@pytest.mark.asyncio
async def test_calendar_list_structured_result_owns_linked_terminal_render(monkeypatch):
    import src.clean_agent_preview as module
    requests = []

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': 'calendar-1', 'function': {
                    'name': 'manage_calendar',
                    'arguments': '{"action":"list_events","start":"2026-09-14","end":"2026-09-21"}',
                },
            }]}}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response()

    linked = (
        'Found 1 event(s) between 2026-09-14 and 2026-09-21:\n'
        '- 2026-09-15T11:00:00 -> 2026-09-15T12:30:00: '
        '[Interior meeting](#event-event-123) (Personal)'
    )

    async def execute(block, **kwargs):
        return 'manage_calendar', {'response': linked, 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_calendar')
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'whats my events this week'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy())]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]

    assert len(requests) == 1
    rendered = next(event['delta'] for event in events if 'delta' in event)
    assert '[Interior meeting](#event-event-123) — Sep 15, 11:00 AM–12:30 PM' in rendered
    assert '2026-09-15T11:00:00' not in rendered
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert metrics['clean_v3_turn'][-1] == {'role': 'assistant', 'content': rendered}


@pytest.mark.asyncio
async def test_whole_email_draft_is_protocol_bound_to_update_document(monkeypatch):
    import src.clean_agent_preview as module
    requests = []
    responses = iter([
        {
            'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': 'write-1', 'function': {
                    'name': 'update_document',
                    'arguments': '{"content":"To: alex@example.com\\nSubject: Re: Meeting\\n---\\nTomorrow works."}',
                },
            }]}}],
            'usage': {'prompt_tokens': 321, 'completion_tokens': 20},
        },
        {
            'choices': [{'delta': {'content': 'Draft ready.'}}],
            'usage': {'prompt_tokens': 400, 'completion_tokens': 9},
        },
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    async def execute(block, **kwargs):
        assert block.tool_type == 'update_document'
        return 'update_document', {
            'output': 'Document updated', 'exit_code': 0,
            'doc_id': 'draft-1', 'title': 'Meeting', 'language': 'email',
            'content': 'To: alex@example.com\nSubject: Re: Meeting\n---\nTomorrow works.',
            'version': 2,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'update_document', 'edit_document', 'suggest_document', 'ui_control',
    }]
    contract = replace(
        resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    draft = SimpleNamespace(
        id='draft-1', title='Meeting', language='email',
        current_content='To: alex@example.com\nSubject: Re: Meeting\n---\nCan we meet tomorrow?',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Write reply to this email'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), active_document=draft)]

    assert [tool['function']['name'] for tool in requests[0]['tools']] == ['update_document']
    assert requests[0]['tool_choice'] == {
        'type': 'function', 'function': {'name': 'update_document'},
    }
    assert 'tools' not in requests[1]
    assert 'tool_choice' not in requests[1]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]
    event_types = [event.get('type') for event in events]
    assert event_types.index('doc_update') < event_types.index('tool_output')
    doc_update = next(event for event in events if event.get('type') == 'doc_update')
    assert doc_update == {
        'type': 'doc_update', 'doc_id': 'draft-1', 'title': 'Meeting',
        'language': 'email',
        'content': 'To: alex@example.com\nSubject: Re: Meeting\n---\nTomorrow works.',
        'version': 2,
    }
    tool_output = next(event for event in events if event.get('type') == 'tool_output')
    assert tool_output['doc_id'] == 'draft-1'
    assert tool_output['document_content'].endswith('Tomorrow works.')
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['injected_tokens'] == 321
    assert metrics['tool_schema_count'] == 1
    assert metrics['agent_rounds'] == 2
    assert metrics['tool_calls'] == 1
    assert metrics['tokens_per_second'] >= 0


@pytest.mark.asyncio
async def test_second_distinct_editor_writer_is_not_executed_after_first_succeeds(monkeypatch):
    import src.clean_agent_preview as module
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [
            {'index': 0, 'id': 'edit-1', 'function': {
                'name': 'edit_document',
                'arguments': '{"edits":[{"find":"Old","replace":"New"}]}',
            }},
            {'index': 1, 'id': 'update-1', 'function': {
                'name': 'update_document', 'arguments': '{"content":"New"}',
            }},
        ]}}]},
        {'choices': [{'delta': {'content': 'Updated.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    executed = []
    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        return 'edit_document', {
            'action': 'edit', 'exit_code': 0, 'doc_id': 'draft-1',
            'title': 'Draft', 'language': 'markdown', 'content': 'New', 'version': 2,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {
        'edit_document', 'update_document',
    }]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    draft = SimpleNamespace(
        id='draft-1', title='Draft', language='markdown', current_content='Old',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Rewrite this draft'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), active_document=draft)]

    assert executed == ['edit_document']
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    outputs = [event for event in events if event.get('type') == 'tool_output']
    assert len(outputs) == 2
    assert outputs[0]['error'] is False
    assert outputs[1]['error'] is False
    assert json.loads(outputs[1]['output'])['already_applied'] is True


@pytest.mark.asyncio
async def test_stream_forwards_successful_panel_open_to_ui(monkeypatch):
    import src.clean_agent_preview as module
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'ui-1', 'function': {
            'name': 'ui_control',
            'arguments': '{"action":"open_panel","name":"gallery"}',
        }}]}}]},
        {'choices': [{'delta': {'content': 'Opened the gallery.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    async def execute(block, **kwargs):
        assert block.tool_type == 'ui_control'
        assert block.content == 'open_panel gallery'
        return 'ui_control', {
            'ui_event': 'open_panel', 'panel': 'gallery',
            'results': 'Opening gallery panel', 'exit_code': 0,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'ui_control')
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Open gallery'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy())]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]
    assert any(e.get('type') == 'tool_start' and e.get('tool') == 'ui_control' for e in events)
    assert any(
        e.get('type') == 'ui_control'
        and (e.get('data') or {}).get('ui_event') == 'open_panel'
        and (e.get('data') or {}).get('panel') == 'gallery'
        for e in events
    )


@pytest.mark.asyncio
async def test_stream_replaces_unsupported_write_completion(monkeypatch):
    import src.clean_agent_preview as module
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'All notes have been deleted.'}}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(endpoint_url='http://test', model='test', messages=[{'role': 'user', 'content': 'Delete all my notes.'}], headers={}, turn_contract=contract, session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy())]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]
    final = next(e['content'] for e in events if e.get('type') == 'final_response')
    assert final == denied_response()
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['clean_v3_turn'] == [{'role': 'assistant', 'content': denied_response()}]


@pytest.mark.asyncio
async def test_native_workspace_write_allows_evidence_backed_completion(monkeypatch):
    import src.clean_agent_preview as module
    requests = []
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'write', 'function': {
                'name': 'write_file',
                'arguments': json.dumps({
                    'path': '/workspace/output.txt', 'content': 'verified result',
                }),
            },
        }]}}]},
        {'choices': [{'delta': {'content': 'Saved the workspace artifact.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    async def execute(block, **kwargs):
        assert block.tool_type == 'write_file'
        return 'write_file', {'output': 'Wrote output.txt', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'write_file')
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())

    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Create a workspace artifact.'}], headers={},
        turn_contract=contract, session_id='test', owner='test', disabled_tools=set(),
        tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
        }, max_rounds=2,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert not [event for event in events if event.get('type') == 'final_response']
    assert any(
        event.get('delta') == 'Saved the workspace artifact.' for event in events
    )
    native_system = requests[0]['messages'][0]['content']
    assert 'batch independent known URLs' in native_system
    assert 'create required artifacts incrementally' in native_system


@pytest.mark.asyncio
async def test_policy_denied_batch_executes_nothing(monkeypatch):
    import src.clean_agent_preview as module
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            calls = [
                {'index': 0, 'id': 'read', 'function': {'name': 'manage_notes', 'arguments': '{"action":"list"}'}},
                {'index': 1, 'id': 'delete', 'function': {'name': 'manage_notes', 'arguments': '{"action":"delete","id":"x"}'}},
            ]
            yield 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': calls}}]})
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    async def must_not_execute(*args, **kwargs):
        raise AssertionError('a partially permitted batch must not execute')
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', must_not_execute)
    notes = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'manage_notes')
    contract = resolve_full_inventory_contract(schemas=[notes], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(endpoint_url='http://test', model='test', messages=[{'role': 'user', 'content': 'List my notes.'}], headers={}, turn_contract=contract, session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy())]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]
    assert not [e for e in events if e.get('type') in {'tool_start', 'tool_output'}]
    assert next(e['content'] for e in events if e.get('type') == 'final_response') == denied_response()


@pytest.mark.asyncio
async def test_native_bash_arguments_use_canonical_execution_content(monkeypatch):
    import src.clean_agent_preview as module
    command = "printf '%s\\n' ODY_SHELL_FILES_READONLY; cat /etc/hostname"
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'shell', 'function': {'name': 'bash', 'arguments': json.dumps({'command': command})}}]}}]},
        {'choices': [{'delta': {'content': 'Marker verified.'}}]},
    ])
    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))
    captured = []
    async def execute(block, **kwargs):
        captured.append(block)
        return 'bash', {'output': 'ODY_SHELL_FILES_READONLY\nkierkegaard', 'exit_code': 0}
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    bash = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'bash')
    contract = resolve_full_inventory_contract(schemas=[bash], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(endpoint_url='http://test', model='test', messages=[{'role': 'user', 'content': 'Run the displayed read-only shell command.'}], headers={}, turn_contract=contract, session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy())]
    assert len(captured) == 1
    assert captured[0].tool_type == 'bash'
    assert captured[0].content == command


@pytest.mark.asyncio
async def test_native_shell_evidence_cannot_finish_with_required_artifact_missing(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'analyze',
            'function': {'name': 'bash', 'arguments': json.dumps({
                'command': 'python3 -c "print(42)"',
            })}}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'write',
            'function': {'name': 'write_file', 'arguments': json.dumps({
                'path': '/workspace/output.html', 'content': '<html>done</html>',
            })}}]}}]},
        {'choices': [{'delta': {'content': 'Created and verified output.html.'}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block.tool_type)
        return block.tool_type, {'output': '42' if block.tool_type == 'bash' else 'saved',
                                 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [schema for schema in FUNCTION_TOOL_SCHEMAS
               if schema['function']['name'] in {'bash', 'write_file'}]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': (
            'Analyze the input with Python, then create /workspace/output.html.'
        )}], turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {'required_artifacts': ['/workspace/output.html']},
        }, max_rounds=4,
    )]

    assert executions == ['bash', 'write_file']
    assert len(requests) == 3
    assert any('Created and verified' in chunk for chunk in raw)


@pytest.mark.asyncio
async def test_native_stream_normalizes_single_clip_exports_before_validation(monkeypatch):
    import src.clean_agent_preview as module
    arguments = json.dumps({
        "path": "/workspace/fixtures/video.mp4",
        "exports": json.dumps([{
            "start": "10",
            "end": "16",
            "output_path": "/workspace/clip.mp4",
        }]),
    })
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": "clip-1",
            "function": {"name": "inspect_media", "arguments": arguments},
        }]}}]},
        {"choices": [{"delta": {"content": "Created the requested clip."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    captured = []

    async def execute(block, **kwargs):
        captured.append(block)
        return "inspect_media", {"output": "clip created", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())

    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Create the requested short video clip."}],
        headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=2,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(captured) == 1
    assert json.loads(captured[0].content) == {
        "path": "/workspace/fixtures/video.mp4",
        "start": "10",
        "end": "16",
        "output_path": "/workspace/clip.mp4",
    }
    assert not [event for event in events if event.get("type") == "tool_output" and event.get("error")]


@pytest.mark.asyncio
async def test_native_stream_explains_how_to_recover_from_timestamp_free_export(monkeypatch):
    import src.clean_agent_preview as module
    malformed = json.dumps({
        "path": "/workspace/fixtures/video.mp4",
        "exports": [{"output_path": "/workspace/diagram.png", "type": "diagram"}],
    })
    corrected = json.dumps({
        "path": "/workspace/fixtures/video.mp4",
        "query": "inspect the room layout",
    })
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "bad-export",
            "function": {"name": "inspect_media", "arguments": malformed},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "inspect-first",
            "function": {"name": "inspect_media", "arguments": corrected},
        }]}}]},
        {"choices": [{"delta": {"content": "I inspected the video before authoring."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    captured = []

    async def execute(block, **kwargs):
        captured.append(block)
        return "inspect_media", {"output": "visual evidence", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())

    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Inspect a video and create a diagram."}],
        headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=3,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    errors = [
        event["output"] for event in events
        if event.get("type") == "tool_output" and event.get("error")
    ]
    assert len(captured) == 1
    assert json.loads(captured[0].content) == json.loads(corrected)
    assert len(errors) == 1
    assert "explicit timestamp plus output_path" in errors[0]
    assert "write_file or python" in errors[0]


@pytest.mark.asyncio
async def test_native_stream_synthesizes_after_first_post_budget_tool_call(monkeypatch):
    import src.clean_agent_preview as module
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "first",
            "function": {
                "name": "inspect_media",
                "arguments": json.dumps({"path": "/workspace/fixtures/video.mp4"}),
            },
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "over-budget",
            "function": {
                "name": "inspect_media",
                "arguments": json.dumps({
                    "path": "/workspace/fixtures/video.mp4",
                    "query": "different evidence",
                }),
            },
        }]}}]},
        {"choices": [{"delta": {"content": "The visual evidence shows a complete result."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "inspect_media", {"output": "visual evidence", "exit_code": 0}

    monkeypatch.setattr(module, "NATIVE_TOOL_CALL_LIMIT", 1)
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())

    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Inspect the video."}], headers={},
        turn_contract=contract, session_id="test", owner="test", disabled_tools=set(),
        tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=20,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(executions) == 1
    budget_errors = [
        event for event in events
        if event.get("type") == "tool_output"
        and "budget exhausted" in event.get("output", "")
    ]
    assert len(budget_errors) == 1
    final = [event for event in events if event.get("type") == "final_response"]
    assert not final
    assert any(
        event.get("type") == "completion_recovery"
        and event.get("reason") == "tool_budget_final_synthesis"
        for event in events
    )
    assert any("The visual evidence shows a complete result." in chunk for chunk in raw)


@pytest.mark.asyncio
async def test_model_choice_budget_preserves_evidence_for_final_answer_without_extra_execution(monkeypatch):
    from dataclasses import replace
    import src.clean_agent_preview as module
    # Exercise the boundary deterministically without coupling this recovery
    # test to the larger production allowance for interactive turns.
    # The budget is the configured agent_max_tool_calls setting; the module
    # constant is only the fallback when no budget is resolvable.
    _tool_budget = 6
    def call(i):
        return {'index': i, 'id': f'call-{i}', 'function': {
            'name': 'web_fetch', 'arguments': json.dumps({'url': f'https://example.org/{i}'})}}
    packets = iter([
        {'choices': [{'delta': {'tool_calls': [call(i) for i in range(6)]}}]},
        {'choices': [{'delta': {'tool_calls': [call(7)]}}]},
        {'choices': [{'delta': {'content': 'Found six source pages; further details are unverified.'}}]},
    ])
    requests, executions = [], []
    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(packets))
    async def execute(block, **kwargs):
        executions.append(block)
        return 'web_fetch', {'output': f'Source page {len(executions)}', 'exit_code': 0}
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'web_fetch')
    contract = replace(resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
        routing_experiment='recent_model_choice')
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', messages=[{'role':'user','content':'Read these public pages.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test', disabled_tools=set(),
        tool_policy=ToolPolicy(), max_tool_calls=_tool_budget)]
    assert len(executions) == 6
    assert len(requests) == 3
    assert 'tools' not in requests[-1]
    assert any(m.get('role') == 'tool' and 'Source page' in m.get('content','') for m in requests[-1]['messages'])
    assert any('tool_budget_final_synthesis' in chunk for chunk in raw)
    assert any('Found six source pages' in chunk for chunk in raw)


@pytest.mark.asyncio
async def test_failed_static_fetch_recovers_once_through_rendered_browser(monkeypatch):
    from dataclasses import replace
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'fetch', 'function': {
            'name': 'web_fetch',
            'arguments': json.dumps({'url': 'https://www.reuters.com/example'}),
        }}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'browser', 'function': {
            'name': 'private_browser',
            'arguments': json.dumps({'action': 'open', 'url': 'https://www.reuters.com/example'}),
        }}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'alternative', 'function': {
            'name': 'web_fetch',
            'arguments': json.dumps({'url': 'https://example.org/report'}),
        }}]}}]},
        {'choices': [{'delta': {'content': 'Reuters blocked both access methods. The alternative report was readable.'}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block.tool_type)
        if block.tool_type == 'web_fetch':
            if json.loads(block.content).get('url') == 'https://example.org/report':
                return 'web_fetch', {'output': 'Independent report with readable evidence.', 'exit_code': 0}
            return 'web_fetch', {'error': 'web_fetch: HTTP 401', 'exit_code': 1}
        return 'private_browser', {
            'output': 'Iframe "DataDome CAPTCHA" Access is temporarily restricted',
            'exit_code': 0,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] in {'web_fetch', 'private_browser'}
    ]
    contract = replace(
        resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Tell me more about that Reuters report.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=4,
    )]

    assert executions == ['web_fetch', 'private_browser', 'web_fetch']
    assert all(
        schema['function']['name'] != 'web_fetch'
        for schema in requests[1].get('tools', [])
    )
    assert any(schema['function']['name'] == 'web_fetch' for schema in requests[2]['tools'])
    assert any('Use another relevant source' in str(msg.get('content')) for msg in requests[2]['messages'])
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(event.get('type') == 'tool_loop_recovery' for event in events)
    assert any('blocked both access methods' in event.get('delta', '') for event in events)


@pytest.mark.asyncio
async def test_duplicate_fetch_does_not_hide_distinct_pagination_request(monkeypatch):
    from dataclasses import replace
    import src.clean_agent_preview as module

    def tool_call(call_id, url):
        return {
            'index': 0, 'id': call_id,
            'function': {'name': 'web_fetch', 'arguments': json.dumps({'url': url})},
        }

    first = 'https://api.example.org/feed?start=0'
    next_page = 'https://api.example.org/feed?start=100'
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [tool_call('first', first)]}}]},
        {'choices': [{'delta': {'tool_calls': [tool_call('duplicate', first)]}}]},
        {'choices': [{'delta': {'tool_calls': [tool_call('next', next_page)]}}]},
        {'choices': [{'delta': {'content': 'Read both pages.'}}]},
    ])
    requests, executions = [], []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *_args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    async def execute(block, **_kwargs):
        executions.append(json.loads(block.content)['url'])
        return 'web_fetch', {'output': 'Readable feed page.', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'web_fetch')
    contract = replace(
        resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Read the public feed pages.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=4,
    )]

    assert executions == [first, next_page]
    assert any('different arguments' in chunk for chunk in raw)
    assert any(
        schema['function']['name'] == 'web_fetch'
        for schema in requests[2].get('tools', [])
    )


@pytest.mark.asyncio
async def test_blocked_search_engine_browser_forces_native_web_search(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'browser-1',
            'function': {
                'name': 'private_browser',
                'arguments': json.dumps({
                    'action': 'open',
                    'url': 'https://www.google.com/search?q=latest+AI+news',
                }),
            },
        }]}}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'search-1',
            'function': {
                'name': 'web_search',
                'arguments': json.dumps({'query': 'latest AI news'}),
            },
        }]}}]},
        {'choices': [{'delta': {'content': (
            'Recent AI developments include new model releases and policy updates. '
            'The search results identify the relevant primary sources and dates for each item, '
            'including publication details, concrete findings, and links readers can inspect '
            'for the full context behind each development.'
        )}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block.tool_type)
        if block.tool_type == 'private_browser':
            return 'private_browser', {
                'output': json.dumps({
                    'url': 'https://www.google.com/sorry/index?continue=search',
                    'text': 'Our systems have detected unusual traffic. Complete the reCAPTCHA.',
                }),
                'exit_code': 0,
            }
        return 'web_search', {
            'output': json.dumps({'results': [{
                'title': 'AI update', 'url': 'https://example.com/ai',
                'snippet': 'A dated current report.',
            }]}),
            'exit_code': 0,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] in {'web_search', 'web_fetch', 'private_browser'}
    ]
    contract = replace(
        resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Open browser and find an AI model release.'}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=4,
    )]

    assert executions == ['private_browser', 'web_search']
    assert requests[1]['tool_choice'] == 'required'
    assert [s['function']['name'] for s in requests[1]['tools']] == ['web_search']
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(
        event.get('type') == 'tool_loop_recovery'
        and event.get('reason') == 'browser_search_blocked_fallback'
        for event in events
    )


@pytest.mark.asyncio
async def test_native_stream_reserves_remaining_budget_for_required_artifact(monkeypatch):
    import src.clean_agent_preview as module

    payloads = [{"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": "scratch-write",
        "function": {"name": "python", "arguments": json.dumps({
            "code": "open('/tmp_workspace/scratch.txt', 'w').write('notes')",
        })},
    }]}}]}]
    payloads.extend([
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": f"search-{index}",
            "function": {"name": "web_search", "arguments": json.dumps({"query": f"topic {index}"})},
        }]}}]}
        for index in range(module.NATIVE_ARTIFACT_RESEARCH_LIMIT - 1)
    ])
    payloads.extend([
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "programmatic-directory-write",
            "function": {"name": "python", "arguments": json.dumps({
                "code": (
                    "from pathlib import Path\n"
                    "p = Path('/tmp_workspace/results/out.md')\n"
                    "p.parent.mkdir(parents=True, exist_ok=True)\n"
                    "p.write_text('evidence')"
                ),
            })},
        }]}}]},
        {"choices": [{"delta": {"content": "Saved."}}]},
    ])
    responses = iter(payloads)
    requests = []
    executed = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs["json"])
            return Response(next(responses))

    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        return block.tool_type, {"output": "ok", "exit_code": 0,
                                 "materialized_artifacts": ["/tmp_workspace/results"]
                                 if block.tool_type == "python" and "out.md" in block.content else []}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schemas = [
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] in {"bash", "python", "web_search", "write_file"}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Research and create the requested output."}],
        headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
            "completion_requirements": {"required_artifacts": ["/tmp_workspace/results"]},
        }, max_rounds=32,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    turn_contract = next(event for event in events if event.get("type") == "turn_contract")
    assert turn_contract["required_artifacts"] == ["/tmp_workspace/results"]
    artifact_boundary_step = next(
        event for event in events
        if event.get("type") == "agent_step"
        and event.get("calls_used") == module.NATIVE_ARTIFACT_RESEARCH_LIMIT
    )
    assert artifact_boundary_step["required_artifact_pending"] is True
    assert artifact_boundary_step["artifact_write_phase"] is False
    recovery = next(
        event for event in events
        if event.get("type") == "completion_recovery"
        and event.get("reason") == "artifact_write_budget_reserved"
    )
    assert recovery["calls_used"] == module.NATIVE_ARTIFACT_RESEARCH_LIMIT
    request_contract = next(
        event for event in events
        if event.get("type") == "agent_step"
        and event.get("stage") == "provider_request"
        and event.get("artifact_write_phase") is True
    )
    assert request_contract["offered_tools"] == ["python"]
    assert "web_search" not in request_contract["offered_tools"]
    assert request_contract["tool_choice"] == {
        "type": "function", "function": {"name": "python"},
    }
    artifact_request = requests[module.NATIVE_ARTIFACT_RESEARCH_LIMIT]
    reserved_names = [tool["function"]["name"] for tool in artifact_request["tools"]]
    assert reserved_names == ["python"]
    assert artifact_request["tool_choice"] == {
        "type": "function", "function": {"name": "python"},
    }
    completion_code_schema = (
        artifact_request["tools"][0]["function"]["parameters"]["properties"]["code"]
    )
    assert "pattern" not in completion_code_schema
    assert "Complete executable Python" in completion_code_schema["description"]
    assert "create one or more files inside" in artifact_request["messages"][-1]["content"]
    assert "Do not pass the directory itself as a file path" in artifact_request["messages"][-1]["content"]
    assert executed[-1] == "python"
    assert any(
        event.get("type") == "tool_output" and event.get("tool") == "python"
        and not event.get("error") for event in events
    )


@pytest.mark.asyncio
async def test_native_stream_reserves_wall_time_for_required_artifact(monkeypatch):
    """A slow research tool must not consume the artifact completion window."""
    import src.clean_agent_preview as module

    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "slow-search",
            "function": {"name": "web_search", "arguments": json.dumps({"query": "topic"})},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "artifact-write",
            "function": {"name": "python", "arguments": json.dumps({
                "code": "open('/tmp_workspace/results/out.md', 'w').write('evidence')",
            })},
        }]}}]},
        {"choices": [{"delta": {"content": "Saved and verified."}}]},
    ])
    requests = []
    now = [0.0]

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs["json"])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        if block.tool_type == "web_search":
            now[0] = 450.0
        return block.tool_type, {"output": "ok", "exit_code": 0,
                                 "materialized_artifacts": ["/tmp_workspace/results"]
                                 if block.tool_type == "python" and "out.md" in block.content else []}

    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schemas = [
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] in {"python", "web_search"}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Research and create the requested output."}],
        headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
            "agent_wall_time_seconds": 600,
            "artifact_completion_reserve_seconds": 180,
            "completion_requirements": {"required_artifacts": ["/tmp_workspace/results"]},
        }, max_rounds=32,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    recovery = next(
        event for event in events
        if event.get("type") == "completion_recovery"
        and event.get("reason") == "artifact_write_time_reserved"
    )
    assert recovery["calls_used"] == 1
    assert recovery["elapsed_seconds"] == 450.0
    assert recovery["completion_reserve_seconds"] == 180.0
    assert executed == ["web_search", "python"]
    assert [tool["function"]["name"] for tool in requests[1]["tools"]] == ["python"]


@pytest.mark.asyncio
async def test_detailed_video_answer_requires_second_focused_inspection(monkeypatch):
    import src.clean_agent_preview as module
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "broad", "function": {
            "name": "inspect_media",
            "arguments": json.dumps({"path": "/workspace/video.mp4", "frames": 1}),
        }}]}}]},
        {"choices": [{"delta": {"content": "There were three events."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "focused", "function": {
            "name": "inspect_media",
            "arguments": json.dumps({
                "path": "/workspace/video.mp4",
                "start": "00:00:10", "end": "00:00:20", "frames": 8,
            }),
        }}]}}]},
        {"choices": [{"delta": {"content": "There were two verified events."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "inspect_media", {
            "output": "Video duration: 00:01:00.000\nTimestamped visual evidence.",
            "exit_code": 0,
        }

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "How many events occur in this video?"}],
        headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=4,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(executions) == 2
    assert len(requests) == 4
    recovery = next(event for event in events if event.get('type') == 'completion_recovery')
    assert recovery['reason'] == 'detailed_video_requires_focused_inspection'
    final = next(event for event in events if event.get('type') == 'final_response')
    assert final['content'] == 'There were two verified events.'
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert metrics['clean_v3_turn'][-1]['content'] == 'There were two verified events.'
    assert not any(
        message.get('content') == 'There were three events.'
        for message in metrics['clean_v3_turn']
    )


@pytest.mark.asyncio
async def test_exact_native_read_gets_tool_free_synthesis_round(monkeypatch):
    from dataclasses import replace
    import src.clean_agent_preview as module

    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "read-1", "function": {
            "name": "read_file",
            "arguments": json.dumps({"path": "/workspace/sample.txt", "limit": 3}),
        }}]}}]},
        {"choices": [{"delta": {"content": "Read the requested three lines."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "read_file", {"output": "alpha\nbeta\ngamma", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(item for item in FUNCTION_TOOL_SCHEMAS if item["function"]["name"] == "read_file")
    contract = replace(
        resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
        required=frozenset({"read_file"}),
        capabilities=frozenset({"shell_files"}),
        active_capabilities=frozenset({"shell_files"}),
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Use read_file to read the first three lines of /workspace/sample.txt."}],
        headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=4,
    )]

    assert len(executions) == 1
    assert len(requests) == 2
    assert 'tools' in requests[0]
    assert 'tools' not in requests[1]
    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert any(event.get('delta') == 'Read the requested three lines.' for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('recovery_kind', ['none', 'budget', 'duplicate'])
async def test_context_overflow_retries_model_request_without_replaying_tool(monkeypatch, recovery_kind):
    from dataclasses import replace
    import httpx
    import src.clean_agent_preview as module
    requests, executions = [], []
    overflow_request = 2 if recovery_kind == 'none' else 3
    _tool_budget = 0
    if recovery_kind == 'budget':
        _tool_budget = 1
    async def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == overflow_request:
            return httpx.Response(400, json={'error': {'message':
                "This model's maximum context length is 4096 tokens. However, "
                "you requested 768 output tokens and your prompt contains at least "
                "3900 input tokens, for a total of at least 4668 tokens."}})
        delta = ({'tool_calls': [{'index': 0, 'id': 'page-read', 'function': {
            'name': 'private_browser', 'arguments': json.dumps({
                'action': 'open', 'url': 'https://example.org'})}}]}
            if len(requests) == 1 else {'content': 'The page was read.'})
        if recovery_kind != 'none' and len(requests) == 2:
            args = ({'action': 'click', 'target': '@e2'} if recovery_kind == 'budget'
                    else {'action': 'find', 'text': 'heading'})
            delta = {'tool_calls': [{'index': 0, 'id': 'blocked-click', 'function': {
                'name': 'private_browser', 'arguments': json.dumps(args)}}]}
        if recovery_kind == 'duplicate' and len(requests) == 1:
            delta['tool_calls'][0]['function']['arguments'] = json.dumps({'action': 'find', 'text': 'heading'})
        return httpx.Response(200, text='data: ' + json.dumps({'choices': [{'delta': delta}]})
                              + '\n\ndata: [DONE]\n\n')
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    async def execute(block, **kwargs):
        executions.append(block)
        return 'page', {'output': 'heading current page\n' + '木' * 7900, 'exit_code': 0}
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'private_browser')
    contract = replace(resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
                       routing_experiment='recent_model_choice')
    prompt = 'Open https://example.org and report the page heading.'
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': prompt}], session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_tool_calls=_tool_budget)]
    assert any('The page was read.' in chunk for chunk in raw)
    assert len(executions) == 1
    assert len(requests) == overflow_request + 1
    before, after = requests[-2:]
    assert after['max_tokens'] == before['max_tokens']
    assert len(json.dumps(after)) < len(json.dumps(before))
    assert {'role': 'user', 'content': prompt} in after['messages']
    assert after.get('tools') == before.get('tools')
    calls = {call['id'] for msg in after['messages'] for call in msg.get('tool_calls', [])}
    assert all(msg['tool_call_id'] in calls for msg in after['messages'] if msg['role'] == 'tool')
    assert not any('_harness_control' in msg for request in requests for msg in request['messages'])
    if recovery_kind == 'budget':
        assert any('budget exhausted' in str(msg.get('content')) for msg in after['messages'])


@pytest.mark.asyncio
@pytest.mark.parametrize('status,context_error,expected_requests', [
    (400, True, 3), (413, True, 3), (400, False, 1), (401, True, 1),
])
async def test_context_recovery_is_bounded_and_not_used_for_other_errors(
        monkeypatch, status, context_error, expected_requests):
    import httpx
    import src.clean_agent_preview as module
    requests = []
    async def handle(request):
        requests.append(request)
        message = ("This model's maximum context length is 16384 tokens. However, "
                   "your messages resulted in 17000 tokens."
                   if context_error else 'Invalid tool schema')
        return httpx.Response(status, json={'error': {'message': message}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Hello'}], session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy())]
    assert len(requests) == expected_requests
    assert not any('"type": "tool_start"' in chunk for chunk in raw)
    assert raw[-1].startswith('event: error\ndata: ')
    assert json.loads(raw[-1].split('data: ', 1)[1])['status'] == status
    assert all('"delta"' not in chunk for chunk in raw)


@pytest.mark.asyncio
async def test_started_model_stream_is_never_retried(monkeypatch):
    import httpx
    import src.clean_agent_preview as module
    requests = []
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial answer"}}]}\n\n'
            raise httpx.ReadError('stream disconnected')
    async def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=BrokenStream())
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Hello'}], session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy())]
    assert len(requests) == 1
    assert sum('Partial answer' in chunk for chunk in raw) == 1


@pytest.mark.asyncio
async def test_pre_content_transport_disconnect_is_retried_once(monkeypatch):
    import httpx
    import src.clean_agent_preview as module
    requests = []

    async def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError('disconnected before response headers')
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"content":"Recovered answer"}}]}\n\n'
            'data: [DONE]\n\n'
        ))

    original_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Hello'}], session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy())]
    assert len(requests) == 2
    assert any('Recovered answer' in chunk for chunk in raw)
    assert not any('encountered an error' in chunk for chunk in raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('transition,transition_result,can_repeat', [
    ({'action': 'open', 'url': 'https://example.org/next'}, {}, True),
    ({'action': 'snapshot'}, {}, False),
    ({'action': 'click', 'target': '@e2'}, {'error': 'Element changed', 'exit_code': 1}, True),
    ({'action': 'read'}, {}, False),
    ({'action': 'open', 'url': 'https://example.org/next'},
     {'blocked': True, 'error': 'Denied', 'exit_code': 1}, False),
])
async def test_browser_can_repeat_observation_after_navigation(
        monkeypatch, transition, transition_result, can_repeat):
    from dataclasses import replace
    import src.clean_agent_preview as module
    actions = [{'action': 'find', 'text': 'heading'},
               transition,
               {'action': 'find', 'text': 'heading'}]
    packets = iter([{'choices': [{'delta': {'tool_calls': [{
        'index': 0, 'id': f'browser-{i}', 'function': {'name': 'private_browser',
        'arguments': json.dumps(args)}}]}}]} for i, args in enumerate(actions)]
        + [{'choices': [{'delta': {'content': 'New page heading.'}}]}])
    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(packets))
    executions = []
    async def execute(block, **kwargs):
        executions.append(json.loads(block.content))
        return 'browser fixture', {
            'output': f'Page evidence {len(executions)}', 'exit_code': 0,
            **(transition_result if len(executions) == 2 else {}),
        }
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'private_browser')
    contract = replace(resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
        routing_experiment='recent_model_choice')
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Inspect the heading, open the next page, then inspect its heading.'}],
        session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy())]
    assert executions == (actions if can_repeat else actions[:2])
    if not transition_result.get('blocked'):
        assert any('already returned evidence' in chunk for chunk in raw) != can_repeat


@pytest.mark.asyncio
@pytest.mark.parametrize('recovers', [True, False])
async def test_empty_search_retry_preserves_evidence_dedupe_and_execution_budget(monkeypatch, recovers):
    from dataclasses import replace
    import src.clean_agent_preview as module
    import src.search as search
    from src.agent_tools.web_tools import WebSearchTool
    arguments = json.dumps({'query': 'documentation domains'})
    packets = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': f'search-{i}',
            'function': {'name': 'web_search', 'arguments': arguments}}]}}]}
        for i in range(3 if recovers else 2)
    ] + [{'choices': [{'delta': {'content': 'Used the available source.'}}]}])
    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(packets))
    queries = []
    def provider(query, **kwargs):
        queries.append(query)
        if len(queries) == 1 or not recovers:
            return 'No search results found.', []
        return 'Example domains are for documentation.', [
            {'title': 'Example Domains', 'url': 'https://www.iana.org/help/example-domains'}]
    async def execute(block, **kwargs):
        return 'web_search', await WebSearchTool().execute(arguments, {})
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    monkeypatch.setattr(search, 'comprehensive_web_search', provider)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'web_search')
    contract = replace(resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
        routing_experiment='recent_model_choice')
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Search for documentation domains.'}],
        session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy())]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    outputs = [event for event in events if event.get('type') == 'tool_output']
    assert len(queries) == 2
    assert outputs[0]['evidence_status'] == 'empty'
    if recovers:
        assert outputs[1]['evidence_status'] == 'available'
        assert 'iana.org' in outputs[1]['output']
        assert outputs[2]['error'] and 'already returned evidence' in outputs[2]['output']
    else:
        assert len(queries) == 2
        assert len(outputs) == 2
        assert all(output['evidence_status'] == 'empty' for output in outputs)
        assert any(
            event.get('type') == 'tool_loop_recovery'
            for event in events
        )


@pytest.mark.asyncio
async def test_native_stream_does_not_reexecute_an_identical_successful_call(monkeypatch):
    import src.clean_agent_preview as module
    arguments = json.dumps({"path": "/workspace/fixtures/video.mp4"})
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "inspect-1", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "inspect-2", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]},
        {"choices": [{"delta": {"content": "Used the existing visual evidence."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "inspect_media", {"output": "four useful frames", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Inspect the video."}], headers={},
        turn_contract=contract, session_id="test", owner="test", disabled_tools=set(),
        tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=3,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(executions) == 1
    duplicate = [
        event for event in events
        if event.get("type") == "tool_output" and event.get("error")
    ]
    assert len(duplicate) == 1
    assert "already returned evidence" in duplicate[0]["output"]
    assert 'tools' not in requests[2]
    assert requests[2]['messages'][-1]['role'] == 'user'
    assert 'finish from the evidence' in requests[2]['messages'][-1]['content'].lower()


@pytest.mark.asyncio
async def test_native_stream_permanently_withholds_tool_when_model_ignores_duplicate_correction(
    monkeypatch,
):
    import src.clean_agent_preview as module
    arguments = json.dumps({"path": "/workspace/fixtures/video.mp4"})
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "inspect-1", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "inspect-2", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "inspect-3", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]},
        {"choices": [{"delta": {"content": "Finished from the existing evidence."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "inspect_media", {"output": "visual evidence", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Inspect the video."}], headers={},
        turn_contract=contract, session_id="test", owner="test", disabled_tools=set(),
        tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=4,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(executions) == 1
    duplicate_errors = [
        event for event in events
        if event.get("type") == "tool_output"
        and "already returned evidence" in event.get("output", "")
    ]
    assert len(duplicate_errors) == 2
    # The first exact duplicate leaves the evidence tool available so a
    # changed request can run.  Ignoring that correction once more suppresses
    # the looping tool and forces a completion-only turn.
    assert any(item['function']['name'] == 'inspect_media' for item in requests[2]['tools'])
    assert 'tools' not in requests[3]


@pytest.mark.asyncio
async def test_native_stream_terminates_after_calling_a_permanently_suppressed_tool(monkeypatch):
    import src.clean_agent_preview as module
    arguments = json.dumps({"path": "/workspace/fixtures/video.mp4"})
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"inspect-{index}", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]}
        for index in range(1, 4)
    ] + [{"choices": [{"delta": {"content": "Final answer from existing evidence."}}]}])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs["json"])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "inspect_media", {"output": "visual evidence", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": "Inspect the video."}], headers={},
        turn_contract=contract, session_id="test", owner="test", disabled_tools=set(),
        tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=20,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(executions) == 1
    assert len(requests) == 4
    assert 'tools' not in requests[3]
    assert 'best concise final answer' in requests[3]['messages'][-1]['content'].lower()
    final = [event for event in events if event.get("type") == "final_response"]
    assert final == []
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert metrics['clean_v3_turn'][-1]['content'] == 'Final answer from existing evidence.'


@pytest.mark.asyncio
async def test_suppressed_evidence_tool_recovers_missing_artifact_with_writer(
    monkeypatch, tmp_path,
):
    import src.clean_agent_preview as module

    output = tmp_path / 'results' / 'report.md'
    output_alias = '/workspace/results/report.md'
    search_arguments = json.dumps({'query': 'arxiv cs.CV 2026-02-25'})
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': f'search-{index}', 'function': {
                'name': 'web_search', 'arguments': search_arguments,
                },
            }]}}]}
        for index in range(1, 4)
    ] + [
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'write-1', 'function': {
                'name': 'write_file', 'arguments': json.dumps({
                    'path': output_alias, 'content': '# Verified report\nEvidence retained.\n',
                }),
            },
        }]}}]},
        {'choices': [{'delta': {'content': 'Created and verified the report.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block.tool_type)
        if block.tool_type == 'write_file':
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text('# Verified report\nEvidence retained.\n')
            return 'write_file', {'output': f'wrote {output}', 'exit_code': 0}
        return 'web_search', {'output': 'useful arxiv evidence', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    monkeypatch.setattr(module, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 100)
    schemas = [
        next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == name)
        for name in ('web_search', 'write_file')
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': (
            'Find cs.CV papers submitted on 2026-02-25 and create '
            f'{output_alias} from the returned evidence.'
        )}],
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace=str(tmp_path),
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {'required_artifacts': [output_alias]},
        }, max_rounds=8,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert executions == ['web_search', 'write_file'], [
        (event.get('type'), event.get('reason'), event.get('tool')) for event in events
    ]
    recovery_events = [
        event for event in events
        if event.get('reason') == 'suppressed_tool_artifact_recovery'
    ]
    assert recovery_events, [
        (event.get('type'), event.get('reason'), event.get('tool'), event.get('output'))
        for event in events
    ]
    recovery = recovery_events[0]
    assert recovery['missing_artifacts'] == [output_alias]
    assert any(
        tool['function']['name'] == 'write_file'
        for tool in requests[4].get('tools', [])
    )
    final = [event for event in events if event.get('type') == 'final_response']
    assert all('repeated a tool call' not in event.get('content', '') for event in final)
    assert output.read_text().startswith('# Verified report')


@pytest.mark.asyncio
async def test_native_stream_stops_reexecuting_an_identical_failed_call(monkeypatch):
    import src.clean_agent_preview as module
    arguments = json.dumps({
        "path": "/workspace/fixtures/video.mp4",
        "exports": [{
            "timestamp": "00:00:00",
            "output_path": "/workspace/clip.mp4",
        }],
    })
    responses = iter([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"clip-{index}", "function": {
            "name": "inspect_media", "arguments": arguments,
        }}]}}]}
        for index in range(1, 5)
    ] + [{"choices": [{"delta": {"content": "Unable to create the clip."}}]}])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return "inspect_media", {
            "error": "a video clip export requires explicit start and end",
            "exit_code": 1,
        }

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": (
            "Inspect the video and save /workspace/clip.mp4."
        )}], headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=5,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert len(executions) == 2
    assert any(event.get('type') == 'tool_loop_recovery' for event in events)
    assert any(s['function']['name'] == 'inspect_media' for s in requests[3]['tools'])
    # Two execution failures plus two ignored correction prompts terminate
    # instead of consuming the remaining round budget with the same no-op call.
    assert len(requests) == 4
    final = [event for event in events if event.get('type') == 'final_response']
    assert len(final) == 1
    assert 'could not complete' in final[0]['content'].lower()
    assert any(
        event.get('type') == 'tool_output'
        and 'already failed twice' in event.get('output', '')
        and event.get('execution_attempted') is False
        for event in events
    )


@pytest.mark.asyncio
async def test_native_stream_recovers_when_declared_workspace_artifact_is_missing(
    monkeypatch, tmp_path,
):
    import src.clean_agent_preview as module
    responses = iter([
        {"choices": [{"delta": {"content": "The collision occurs at 00:00:15."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "clip-1", "function": {
            "name": "inspect_media",
            "arguments": json.dumps({
                "path": "/workspace/fixtures/video.mp4",
                "start": "00:00:12", "end": "00:00:18",
                "output_path": "/workspace/clip.mp4",
            }),
        }}]}}]},
        {"choices": [{"delta": {"content": "Saved /workspace/clip.mp4."}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs["json"])
            return Response(next(responses))

    async def execute(block, **kwargs):
        (tmp_path / "clip.mp4").write_bytes(b"video")
        return "inspect_media", {
            "output": "Created clip: /workspace/clip.mp4", "exit_code": 0,
            "output_path": "/workspace/clip.mp4",
        }

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test",
        messages=[{"role": "user", "content": (
            "Inspect /workspace/fixtures/video.mp4 and save the collision clip "
            "to /workspace/clip.mp4."
        )}], headers={}, turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace=str(tmp_path),
        client_runtime_context={
            "surface": "odysseus-native", "terminal_agent": True,
            "unattended_mode": True,
        }, max_rounds=4,
    )]

    assert (tmp_path / "clip.mp4").read_bytes() == b"video"
    assert len(requests) == 3
    assert all(
        message['role'] != 'system'
        for message in requests[1]['messages'][1:]
    )
    assert requests[1]['messages'][-1]['role'] == 'user'
    assert any(
        event.get("type") == "completion_recovery"
        for event in (json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk)
    )
    events = [json.loads(chunk[6:]) for chunk in raw if "[DONE]" not in chunk]
    assert not any(
        event.get("type") == "final_response"
        and event.get("content") == denied_response()
        for event in events
    )


@pytest.mark.asyncio
async def test_preview_propagates_active_document_and_truthfully_marks_tool_error(monkeypatch):
    import src.clean_agent_preview as module
    arguments = json.dumps({'edits': [{'find': 'alpha', 'replace': 'beta'}]})
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'edit', 'function': {
            'name': 'edit_document', 'arguments': arguments,
        }}]}}]},
        {'choices': [{'delta': {'content': 'Could not edit.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(responses))

    captured = {}
    async def execute(block, **kwargs):
        captured.update(kwargs)
        return 'edit_document', {'error': 'FIND did not match'}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    edit = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'edit_document')
    contract = resolve_full_inventory_contract(schemas=[edit], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Edit my document.'}], headers={},
        turn_contract=contract, session_id='test', owner='test', disabled_tools=set(),
        tool_policy=ToolPolicy(), active_document=SimpleNamespace(
            id='doc-123', title='Fixture', language='text', current_content='alpha',
        ))]
    events = [json.loads(s[6:]) for s in raw if '[DONE]' not in s]
    output = next(e for e in events if e.get('type') == 'tool_output')
    assert captured['active_document_id'] == 'doc-123'
    assert output['error'] is True
    assert output['exit_code'] == 1


def test_preview_stream_connections_are_not_reused_between_tool_rounds():
    import src.clean_agent_preview as module

    limits = module.preview_http_limits()

    assert limits.max_keepalive_connections == 0
@pytest.mark.asyncio
async def test_native_stream_stops_fourth_full_rewrite_to_same_target(monkeypatch):
    import src.clean_agent_preview as module

    packets = [
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': f'write-{i}',
            'function': {'name': 'write_file', 'arguments': json.dumps({
                'path': '/tmp_workspace/results/report.md', 'content': f'version {i}',
            })}}]}}]}
        for i in range(1, 5)
    ] + [{'choices': [{'delta': {'content': 'Finished from the latest saved report.'}}]}]
    responses = iter(packets)
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return 'write_file', {'output': 'saved', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'write_file')
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': 'Create the requested report.'}],
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
            'completion_requirements': {'required_artifacts': ['/tmp_workspace/results/report.md']},
        }, max_rounds=8,
    )]

    assert len(executions) == module.SAME_TARGET_WRITE_LIMIT
    assert 'tools' not in requests[-1]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert any(event.get('type') == 'tool_loop_recovery' for event in events)


@pytest.mark.asyncio
async def test_native_stream_redirects_repeated_filename_image_inference(monkeypatch):
    import src.clean_agent_preview as module

    commands = [
        'find images -name "*.jpg" -exec sh -c \'echo "$1" | grep -q "chart"\' _ {} \\;',
        'find images -name "*.jpg" -exec sh -c \'echo "$1" | grep -q "photo"\' _ {} \\;',
    ]
    packets = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'bash-1',
            'function': {'name': 'bash', 'arguments': json.dumps({'command': commands[0]})}}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'bash-2',
            'function': {'name': 'bash', 'arguments': json.dumps({'command': commands[1]})}}]}}]},
        {'choices': [{'delta': {'content': 'Classified it from visual evidence.'}}]},
    ])
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(packets))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block.tool_type)
        return block.tool_type, {'output': 'visual evidence', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] in {'bash', 'inspect_media'}
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': 'Classify these images by what they show.'}],
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
        }, max_rounds=6,
    )]

    assert executions == ['bash']
    assert 'inspect_media' in [
        schema['function']['name'] for schema in requests[-1].get('tools', [])
    ]


def test_shell_native_tool_misuse_only_matches_an_offered_tool():
    import src.clean_agent_preview as module

    inspect_schema = next(
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] == 'inspect_media'
    )
    output = 'bash: line 2: inspect_media: command not found\n'
    assert module.shell_native_tool_misuse(output, [inspect_schema]) == 'inspect_media'
    assert module.shell_native_tool_misuse(output, []) == ''
    assert module.shell_native_tool_misuse('ordinary command output', [inspect_schema]) == ''


def test_shell_native_tool_command_misuse_only_matches_an_offered_tool():
    import src.clean_agent_preview as module

    inspect_schema = next(
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] == 'inspect_media'
    )
    offered = [inspect_schema]
    assert module.shell_native_tool_command_misuse(
        'pip install inspect_media', offered,
    ) == 'inspect_media'
    assert module.shell_native_tool_command_misuse(
        "python3 -c 'from inspect_media import inspect_image'", offered,
    ) == 'inspect_media'
    assert module.shell_native_tool_command_misuse('pip install inspect_media', []) == ''


def test_shell_sensitive_command_error_never_reflects_the_secret():
    import src.clean_agent_preview as module

    command = 'export OPENROUTER_API_KEY=sk-example-secret-value'
    error = module.shell_sensitive_command_error(command)
    assert 'OPENROUTER_API_KEY' in error
    assert 'sk-example-secret-value' not in error
    assert module.shell_sensitive_command_error('echo "$OPENROUTER_API_KEY"')
    assert module.shell_sensitive_command_error('echo ordinary-value') == ''


def test_masked_shell_pipeline_failure_accepts_adapter_combined_output():
    import src.clean_agent_preview as module

    error = module.masked_shell_pipeline_failure({
        'output': "find: 'images': No such file or directory",
        'exit_code': 0,
    })
    assert error == "find: 'images': No such file or directory"


def test_semantic_repeat_scope_distinguishes_focused_still_image_inspections():
    import src.clean_agent_preview as module

    first = module.semantic_repeat_scope(
        'inspect_media', {'path': '/workspace/map.png', 'query': 'read labels'},
    )
    second = module.semantic_repeat_scope(
        'inspect_media', {'path': '/workspace/map.png', 'query': 'identify colors'},
    )
    assert first == ('still_image_inspection', '/workspace/map.png', 'read labels', 0)
    assert second != first
    assert module.semantic_repeat_scope(
        'inspect_media', {'path': '/workspace/map.png', 'query': '  READ   labels '},
    ) == first
    assert module.semantic_repeat_scope(
        'inspect_media', {'path': '/workspace/video.mp4', 'start': 0, 'end': 10},
    ) is None


@pytest.mark.asyncio
async def test_native_stream_allows_focused_reinspection_but_blocks_exact_repeat(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'inspect-1',
            'function': {'name': 'inspect_media', 'arguments': json.dumps({
                'path': '/workspace/map.png', 'query': 'read labels',
            })}}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'inspect-2',
            'function': {'name': 'inspect_media', 'arguments': json.dumps({
                'path': '/workspace/map.png', 'query': 'identify colors',
            })}}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'inspect-3',
            'function': {'name': 'inspect_media', 'arguments': json.dumps({
                'path': '/workspace/map.png', 'query': 'identify colors',
            })}}]}}]},
        {'choices': [{'delta': {'content': 'Finished from the existing visual evidence.'}}]},
    ])

    class Response:
        is_error = False
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        return 'inspect_media', {'output': 'visual evidence', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    inspect_schema = next(
        schema for schema in FUNCTION_TOOL_SCHEMAS
        if schema['function']['name'] == 'inspect_media'
    )
    contract = resolve_full_inventory_contract(
        schemas=[inspect_schema], policy=ToolPolicy(),
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': 'Inspect the map.'}],
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
        }, max_rounds=5,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert len(executions) == 2
    duplicate = [
        event for event in events
        if event.get('type') == 'tool_output' and event.get('error')
    ]
    assert len(duplicate) == 1
    assert 'already inspected' in duplicate[0]['output'].lower()
    assert 'inspect_media' not in [
        schema['function']['name'] for schema in requests[3].get('tools', [])
    ]


@pytest.mark.asyncio
async def test_native_stream_marks_masked_shell_pipeline_failure_as_error(monkeypatch):
    """A successful final pipe stage must not hide an earlier shell failure."""
    import src.clean_agent_preview as module

    packets = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'bash-1',
            'function': {'name': 'bash', 'arguments': json.dumps({
                'command': 'ls /missing-directory | head',
            })}}]}}]},
        {'choices': [{'delta': {'content': 'The directory could not be listed.'}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(packets))

    async def execute(block, **kwargs):
        return 'bash', {
            'output': "STDERR: ls: cannot access '/missing-directory': No such file or directory",
            'stderr': "ls: cannot access '/missing-directory': No such file or directory",
            'exit_code': 0,
        }

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    bash = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'bash')
    contract = resolve_full_inventory_contract(schemas=[bash], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': 'List the directory.'}],
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
        }, max_rounds=4,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    output = next(event for event in events if event.get('type') == 'tool_output')
    assert output['error'] is True
    assert output['exit_code'] == 1
    assert 'No such file or directory' in output['output']


@pytest.mark.asyncio
async def test_parallel_tool_results_precede_visual_evidence(monkeypatch):
    import src.clean_agent_preview as module

    responses = iter([
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "inspect", "function": {
                "name": "inspect_media", "arguments": json.dumps({"path": "/workspace/input.png"})}},
            {"index": 1, "id": "list", "function": {
                "name": "ls", "arguments": json.dumps({"path": "/workspace"})}},
        ]}}]},
        {"choices": [{"delta": {"content": "Done."}}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(self.payload)
            yield "data: [DONE]"

    requests = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs["json"])
            return Response(next(responses))

    async def execute(block, **kwargs):
        if block.tool_type == "inspect_media":
            return "inspect_media", {
                "output": "image evidence", "exit_code": 0,
                "images": [{"mimeType": "image/png", "data": "aQ=="}],
            }
        return "ls", {"output": "input.png", "exit_code": 0}

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "execute_tool_block", execute)
    schemas = [
        next(item for item in FUNCTION_TOOL_SCHEMAS if item["function"]["name"] == name)
        for name in ("inspect_media", "ls")
    ]
    contract = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    _ = [chunk async for chunk in stream_preview(
        endpoint_url="http://test", model="test", headers={},
        messages=[{"role": "user", "content": "Inspect and list."}],
        turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace="/tmp/workspace",
        client_runtime_context={"surface": "odysseus-native", "terminal_agent": True,
                                "unattended_mode": True}, max_rounds=2,
    )]

    messages = requests[1]["messages"]
    assistant_index = max(i for i, message in enumerate(messages) if message["role"] == "assistant")
    assert [message["role"] for message in messages[assistant_index + 1:]] == [
        "tool", "tool", "user", "user",
    ]
    assert "Final completion round" in messages[-1]["content"]
    assert messages[-2]["content"][1]["type"] == "image_url"
@pytest.mark.asyncio
async def test_tool_round_leadin_is_replaced_by_terminal_synthesis(monkeypatch):
    from dataclasses import replace
    import src.clean_agent_preview as module

    packets = iter([
        {'choices': [{'delta': {
            'content': "I'll browse IKEA and look at their chairs.",
            'tool_calls': [{'index': 0, 'id': 'browse-1', 'function': {
                'name': 'private_browser',
                'arguments': json.dumps({
                    'action': 'open',
                    'url': 'https://www.ikea.com/us/en/cat/armchairs-16239/',
                }),
            }}],
        }}]},
        {'choices': [{'delta': {
            'reasoning_content': 'I have enough. Now compose the final answer.',
            'content': 'The most epic option is the DYVLINGE swivel chair.',
        }}]},
    ])

    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response(next(packets))

    async def execute(block, **kwargs):
        return 'private_browser', {'output': 'IKEA chair evidence', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'private_browser'
    )
    contract = replace(
        resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
        routing_experiment='recent_model_choice',
    )
    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='deepseek-test', headers={},
        turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Go to ikea.com and find the most epic chair.'}],
        session_id='test', owner='test', disabled_tools=set(),
        tool_policy=ToolPolicy(), max_rounds=3,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    leadin = next(event for event in events if event.get('delta', '').startswith("I'll browse"))
    assert 'replacement_scope' not in leadin
    synthesis = next(
        event for event in events
        if event.get('delta', '').startswith('The most epic option')
    )
    assert synthesis['render_owner'] == 'streamed'
    assert synthesis['replacement_scope'] == 'turn'
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assistant_rounds = [
        item.get('content')
        for item in metrics['clean_v3_turn']
        if item.get('role') == 'assistant' and item.get('content')
    ]
    assert assistant_rounds == [
        "I'll browse IKEA and look at their chairs.",
        'The most epic option is the DYVLINGE swivel chair.',
    ]
