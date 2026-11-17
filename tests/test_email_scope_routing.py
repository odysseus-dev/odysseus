import json

import pytest

from src.turn_contract import _routing_email_scope, requested_capabilities, selected_tools_for_request


@pytest.mark.parametrize('folder', ['Archive', 'Receipts', '"Sent Items"', 'Finance/2026'])
@pytest.mark.parametrize('account', ['work@example.com', 'bills+tax@sub.example.org'])
def test_mailbox_location_is_not_a_filesystem_or_browser_operation(folder, account):
    lookup = f'Find and read the latest email about the design review. Use the {folder} folder on {account}.'
    assert selected_tools_for_request(lookup) == {'search_emails', 'read_email'}
    assert requested_capabilities(lookup) == {'email'}
    question = f'Any update on the design review in my email? Read the newest message. Use the {folder} folder on {account}.'
    assert requested_capabilities(question) == {'email'}


@pytest.mark.parametrize('prompt', [
    'Read my email. Create a folder in /workspace for the report.',
    'Search my email. Use the Archive folder and run a Python script.',
    'Use the Archive folder on server.example.com.',
    'Archive the email from Morgan.',
])
def test_independent_actions_are_not_removed(prompt):
    assert _routing_email_scope(prompt) == prompt


@pytest.mark.parametrize('prompt', [
    'What time did Morgan send the latest email about the design review?',
    'When was the newest email from Morgan received?',
    'Find the last email regarding the contract review.',
])
def test_concrete_email_reference_beats_generic_latest_review_heuristics(prompt):
    assert requested_capabilities(prompt) == {'email'}


@pytest.mark.parametrize('prompt', [
    'Find reviews of the latest email clients.',
    'What are the latest events in Kyiv?',
])
def test_public_topics_are_not_mailbox_references(prompt):
    assert requested_capabilities(prompt) == {'search_browser'}


@pytest.mark.asyncio
async def test_source_update_report_is_not_an_unperformed_edit(monkeypatch):
    from src import clean_agent_preview as preview
    from src import email_task_intent
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract

    answer = 'Morgan updated the design review to 14:45 UTC.'
    responses = iter([
        {'tool_calls': [{'index': 0, 'id': 'read1', 'type': 'function', 'function': {
            'name': 'read_email', 'arguments': '{"uid":"73"}'}}]},
        {'content': answer},
    ])

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': next(responses)}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()

    async def classify(*args, **kwargs):
        return email_task_intent.EmailTaskIntent('read', ('email',), 'Read the update', requires_content=True)

    async def execute(block, **kwargs):
        assert block.tool_type.removeprefix('mcp__email__') == 'read_email'
        return 'read_email', {'exit_code': 0, 'body': answer, 'uid': '73'}

    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(email_task_intent, 'classify_email_task', classify)
    monkeypatch.setattr(preview, 'execute_tool_block', execute)
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'read_email']
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    chunks = [c async for c in preview.stream_preview(
        endpoint_url='http://test', model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Any update on the design review in my inbox? Read message UID 73.'}],
        session_id='fixture-read-update', owner='fixture', disabled_tools=set(), tool_policy=policy, max_rounds=3,
    )]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    final = next((e['content'] for e in reversed(events) if e.get('type') == 'final_response'),
                 ''.join(e.get('delta', '') for e in events))
    assert final == answer, events
