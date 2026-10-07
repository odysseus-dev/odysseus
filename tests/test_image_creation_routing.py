import pytest
from src.turn_contract import requested_capabilities, selected_tools_for_request, resolve_turn_contract, resolve_full_inventory_contract
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_policy import ToolPolicy
from src.clean_agent_preview import PREVIEW_TOOLS, scope_preview_contract, preview_call_allowed


def test_compact_image_tool_uses_configured_model():
    from src.clean_agent_preview import compact_schemas
    original = next(s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'generate_image')
    compact = compact_schemas([original], model='Ajax')[0]['function']['parameters']
    assert 'model' not in compact['properties']
    assert 'model' in original['function']['parameters']['properties']


def test_image_revision_uses_edit_contract_not_fresh_generation():
    history = [{'role': 'assistant', 'metadata': {'tool_events': [
        {'tool': 'generate_image', 'exit_code': 0, 'image_id': 'source-image'}
    ]}}]
    for prompt in ['Add another cow', 'Make it brighter', 'Remove the fence', 'Change the sky to blue']:
        capabilities = requested_capabilities(prompt, history)
        assert capabilities == {'image_editing'}
        contract = resolve_turn_contract(capabilities=capabilities, schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy(), message=prompt)
        assert 'edit_image' in contract.required
        assert 'generate_image' not in contract.offered
        assert preview_call_allowed('edit_image', {'image_id': 'source-image', 'action': 'prompt', 'prompt': prompt}, prompt,
                                    contract_required_tools=contract.required, turn_authorized_families=capabilities)
    assert requested_capabilities('Make a new image of a cat', history) == {'image_generation'}
    assert 'image_editing' not in requested_capabilities('Create a task to add another cow', history)
    assert 'image_editing' not in requested_capabilities('Add another cow', history + [{'role': 'assistant', 'content': 'Unrelated answer'}])


def test_uploaded_image_edit_is_not_ocr_or_description():
    assert requested_capabilities('Make more realistic', image_attachment=True) == {'image_editing'}
    assert 'image_editing' not in requested_capabilities('Describe this image', image_attachment=True)
    assert 'image_editing' not in requested_capabilities('Make sense of this image', image_attachment=True)
    assert requested_capabilities('Make a new image of a cat', image_attachment=True) == {'image_generation'}


@pytest.mark.parametrize('prompt', [
    'make a thumbnail of a background for title : releasing my own ai',
    'Makes image of a cow', 'Creates an image of a cow',
    'Generates a picture of a cow', 'Make an. Image of a cow',
    'Image of a cow', 'A picture of a lighthouse', 'Please an illustration of a city',
])
def test_thumbnail_survives_live_inventory_pipeline(prompt):
    from src.clean_agent_preview import INTERACTIVE_CORE_TOOLS
    capabilities = requested_capabilities(prompt)
    selected = selected_tools_for_request(prompt)
    routed = resolve_turn_contract(capabilities=capabilities, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(), selected_tools=selected, required_tools=selected, message=prompt)
    preview = resolve_full_inventory_contract(
        schemas=[s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in PREVIEW_TOOLS],
        policy=ToolPolicy())
    scoped = scope_preview_contract(preview, routed, capabilities, extra_tools=INTERACTIVE_CORE_TOOLS)
    assert 'generate_image' in scoped.required
    assert not scoped.unavailable


def test_missing_requested_capability_cannot_fall_back_to_core_tools():
    from src.clean_agent_preview import INTERACTIVE_CORE_TOOLS
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] != 'generate_image']
    routed = resolve_turn_contract(capabilities={'image_generation'}, schemas=schemas,
        policy=ToolPolicy(), selected_tools={'generate_image'}, required_tools={'generate_image'})
    preview = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    scoped = scope_preview_contract(preview, routed, {'image_generation'},
                                    extra_tools=INTERACTIVE_CORE_TOOLS)
    assert not scoped.offered
    assert 'capability:image_generation' in scoped.unavailable


@pytest.mark.parametrize('prompt', [
    'can you make a youtube thumbnail for title releasing my own ai',
    'Generate an illustration of a city', 'Draw a logo for my bakery',
    'Make an image about email', 'Create a poster about writing documents',
    'Make an. Image of a cow', 'Make a, picture of a cow',
    'Makes image of a cow', 'Draws a picture of a cow',
])
def test_explicit_visual_creation_routes_to_generator(prompt):
    assert requested_capabilities(prompt) == {'image_generation'}
    assert selected_tools_for_request(prompt) == {'generate_image'}


@pytest.mark.parametrize('prompt', [
    'Draft an email about a thumbnail', 'Write a document about image generation',
    'Create a task to generate images daily', 'Create a note about drawing a logo',
    'Explain how to make a thumbnail', 'Do not generate an image',
    'Find an image of a cow', 'Search for a picture of a cow',
    'Describe this image of a cow', 'Write a note about an image of a cow',
])
def test_other_creation_does_not_expose_generator(prompt):
    assert 'image_generation' not in requested_capabilities(prompt)
    assert 'generate_image' not in (selected_tools_for_request(prompt) or ())


def test_image_with_explicit_document_insertion():
    prompt = 'Make an illustration and insert it into this document'
    assert requested_capabilities(prompt) == {'image_generation', 'documents'}
    assert selected_tools_for_request(prompt) == {'generate_image', 'update_document'}


def test_generator_policy_and_scope():
    assert 'generate_image' in PREVIEW_TOOLS
    assert preview_call_allowed('generate_image', {'prompt': 'A city'},
                                'Make an image of a city', contract_required_tools={'generate_image'},
                                turn_authorized_families={'image_generation'})
    assert not preview_call_allowed('generate_image', {'prompt': 'A city'}, 'Write an email')
    preview = resolve_full_inventory_contract(schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy())
    routed = resolve_turn_contract(capabilities={'notes'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(), selected_tools={'manage_notes'}, message='Create a note')
    scoped = scope_preview_contract(preview, routed, {'notes'}, extra_tools={'generate_image'})
    assert 'generate_image' not in scoped.offered


def test_disabled_generation_is_not_restored():
    contract = resolve_turn_contract(capabilities={'image_generation'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(disabled_tools={'generate_image'}), selected_tools={'generate_image'},
        required_tools={'generate_image'}, message='Make an image')
    assert 'generate_image' not in contract.offered


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_code', [None, 0])
async def test_generation_dispatch_uses_owner_aware_backend(monkeypatch, exit_code):
    from types import SimpleNamespace
    from src import ai_interaction, tool_execution
    from src.agent_runtime.journal import ActionJournal, bind_journal
    from tests.runtime_evidence_helpers import server_authorized_executor
    execute = server_authorized_executor(tool_execution.execute_tool_block)
    calls = []
    async def generate(content, **kwargs):
        calls.append((content, kwargs))
        result = {'image_url': '/api/generated-image/test.png', 'image_id': 'test'}
        if exit_code is not None:
            result['exit_code'] = exit_code
        return result
    async def legacy(*args, **kwargs):
        pytest.fail('Native generation must not use the ownerless MCP adapter')
    monkeypatch.setattr(ai_interaction, 'do_generate_image', generate)
    monkeypatch.setattr(tool_execution, '_call_mcp_tool', legacy)
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    block = SimpleNamespace(tool_type='generate_image', content='{"prompt":"A city"}')
    journal = ActionJournal()
    with bind_journal(journal):
        _, denied = await execute(block, owner='pewds', session_id='fixture',
            disabled_tools={'generate_image'}, security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT)
        _, result = await execute(block, owner='pewds', session_id='fixture',
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT)
    assert denied['exit_code'] != 0
    assert journal.actions[0].execution_id is None
    assert not journal.actions[0].outcome['authoritative']
    assert journal.actions[1].execution_id is not None
    assert journal.actions[1].outcome['authoritative'] is (exit_code == 0)
    assert result['image_id'] == 'test'
    assert calls == [(block.content, {'owner': 'pewds', 'session_id': 'fixture'})]


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_generation_stream_forwards_only_successful_images(monkeypatch, failed):
    import json
    import src.clean_agent_preview as runtime
    replies = iter([
        {'tool_calls': [{'index': 0, 'id': 'image-1', 'function': {'name': 'generate_image',
            'arguments': json.dumps({'prompt': 'YouTube thumbnail: releasing my own AI'})}}]},
        {'content': 'Image generation is unavailable.' if failed else 'Here is the generated thumbnail.'},
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
        def stream(self, *args, **kwargs): return Response(next(replies))
    calls = []
    async def execute(block, **kwargs):
        from src.tool_execution import _build_mcp_args
        assert block.tool_type == 'generate_image'
        calls.append(_build_mcp_args(block.tool_type, block.content))
        if failed:
            return block.tool_type, {'exit_code': 1, 'error': 'No image endpoint configured'}
        return block.tool_type, {'exit_code': 0, 'stdout': 'Saved /api/generated-image/fixture.png',
                                'image_url': '/api/generated-image/fixture.png', 'image_id': 'fixture'}
    monkeypatch.setattr(runtime.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(runtime, 'execute_tool_block', execute)
    contract = resolve_turn_contract(capabilities={'image_generation'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(), selected_tools={'generate_image'}, required_tools={'generate_image'},
        message='Make a YouTube thumbnail')
    chunks = [c async for c in runtime.stream_preview(endpoint_url='http://test', model='Ajax', headers={},
        turn_contract=contract, messages=[{'role': 'user', 'content': 'Make a YouTube thumbnail'}],
        session_id='fixture', owner='fixture', disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=3)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    images = [e for e in events if e.get('type') == 'generated_image']
    assert len(calls) == 1
    assert bool(images) is not failed
    if images:
        assert images[0]['url'] == '/api/generated-image/fixture.png'
        assert images[0]['image_id'] == 'fixture'
