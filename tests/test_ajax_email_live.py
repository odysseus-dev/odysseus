"""Opt-in real Ajax/harness checks; every tool execution uses local fixtures.

ODYSSEUS_AJAX_TEST_URL=http://host:port/v1/chat/completions pytest -s tests/test_ajax_email_live.py
No app server, account, mailbox, browser, or editor mutations are used.
"""
import json
import os
from types import SimpleNamespace

import pytest

from src.clean_agent_preview import stream_preview
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract

URL = os.environ.get('ODYSSEUS_AJAX_TEST_URL')
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason='opt-in live Ajax endpoint')]


async def test_live_named_recipient_is_resolved_before_draft(monkeypatch):
    import src.clean_agent_preview as module
    calls = []

    async def execute(block, **kwargs):
        name = module.canonical(block.tool_type)
        args = json.loads(block.content)
        calls.append((name, args))
        if name == 'resolve_contact':
            return name, {'exit_code': 0, 'output': json.dumps({'matches': [
                {'name': 'Jonathan Amos', 'email': 'jonathan.amos@example.com'}]})}
        assert name == 'draft_email'
        assert calls[0][0] == 'resolve_contact'
        assert args['to'] == 'jonathan.amos@example.com'
        return name, {'exit_code': 0, 'output': 'Created unsent fixture draft.', 'doc_id': 'fixture'}

    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'resolve_contact']
    schemas.append({'type': 'function', 'function': {'name': 'mcp__email__draft_email',
        'description': 'Create an unsent email draft.', 'parameters': {'type': 'object',
        'properties': {k: {'type': 'string'} for k in ('to', 'subject', 'body')},
        'required': ['to', 'subject', 'body']}}})
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    chunks = [chunk async for chunk in stream_preview(
        endpoint_url=URL, model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'Write an email to jonathan saying was nice to hang'}],
        session_id='fixture-contact-draft', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=5,
    )]
    assert not any(c.startswith('event: error') for c in chunks), chunks
    assert [name for name, _ in calls] == ['resolve_contact', 'draft_email'], chunks


@pytest.mark.parametrize('prompt,requires_body', [
    ('What time did Morgan send the latest email about the design review?', False),
    ('Who sent the latest invoice email in my inbox?', False),
    ('What is the subject of the last email from Morgan?', False),
    ('When does the design review start? Check my mail.', True),
    ('How much do I owe on the invoice in my latest email?', True),
])
async def test_live_ajax_email_evidence_requirement(prompt, requires_body):
    import httpx
    from src.email_task_intent import classify_email_task

    async with httpx.AsyncClient() as client:
        intent = await classify_email_task(client, endpoint_url=URL, headers={}, model='Ajax',
            history=[{'role': 'user', 'content': prompt}])
    assert intent.operation == 'read'
    assert 'email' in intent.dependencies
    assert intent.requires_content is requires_body, intent


@pytest.mark.parametrize('prompt', [
    "Find and read Morgan's latest email about the design review. What time is it? Do not draft or send a reply.",
    "Find and read Morgan's latest email about the design review. What time does the design review start? Do not draft or send a reply.",
    'Look in my email. When is the design review?',
    'Any update on the design review in my inbox? Read the newest message.',
    'What time did Morgan say the review starts? Check my mail.',
    'What time did Morgan send the latest email about the design review?',
])
@pytest.mark.parametrize('folder,account', [('INBOX', 'fixture@example.com'), ('Archive', 'work@example.com')], ids=['inbox', 'archive'])
async def test_live_ajax_latest_email_is_not_web_discovery(monkeypatch, prompt, folder, account, prior=()):
    import src.clean_agent_preview as module
    from src.turn_contract import resolve_turn_contract, requested_capabilities, selected_tools_for_request

    if folder != 'INBOX':
        prompt = prompt.replace('in my inbox', 'in my email') + f' Use the {folder} folder on {account}.'
    executions = []

    async def execute(block, **kwargs):
        name = block.tool_type.removeprefix('mcp__email__')
        args = json.loads(block.content)
        executions.append((name, args))
        if name in {'search_emails', 'list_emails'}:
            return name, {'exit_code': 0, 'results': [{'uid': '72', 'folder': folder,
                'account': account, 'from': 'Morgan <morgan@example.com>',
                'subject': 'Design review', 'date': '2026-09-28T09:00:00Z'}, {'uid': '73', 'folder': folder,
                'account': account, 'from': 'Morgan <morgan@example.com>',
                'subject': 'Design review', 'date': '2026-09-30T09:00:00Z'}]}
        if name == 'read_email' and (
            args.get('folder', 'INBOX') != folder or args.get('account', 'fixture@example.com') != account
        ):
            return name, {'exit_code': 1, 'error': 'No matching message in this account and folder.'}
        if name == 'read_email' and str(args.get('uid')) == '73':
            return name, {'exit_code': 0, 'uid': '73', 'subject': 'Design review',
                'body': 'Update: the design review is October 1, 2026 at 14:45 UTC, not 10:00 as previously planned. Bring the revised drawings.'}
        if name == 'read_email' and str(args.get('uid')) == '72':
            return name, {'exit_code': 0, 'uid': '72', 'subject': 'Design review',
                'body': 'The design review is October 1, 2026 at 10:00 UTC.'}
        return name, {'exit_code': 1, 'error': 'Fixture only permits searching and reading the listed email.'}

    monkeypatch.setattr(module, 'execute_tool_block', execute)
    policy = ToolPolicy()
    selected = selected_tools_for_request(prompt)
    contract = resolve_turn_contract(capabilities=requested_capabilities(prompt, prior),
        schemas=FUNCTION_TOOL_SCHEMAS, policy=policy, selected_tools=selected,
        required_tools=selected or (), message=prompt)
    chunks = [chunk async for chunk in stream_preview(
        endpoint_url=URL, model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': prompt}],
        history_session=SimpleNamespace(history=list(prior)),
        session_id='fixture-latest-email', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=4)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    answer = next((e['content'] for e in reversed(events) if e.get('type') == 'final_response'),
                  ''.join(e.get('delta', '') for e in events))
    print(json.dumps({'answer': answer, 'executions': executions,
        'scope': [e for e in events if e.get('stage') == 'email_task_scope'],
        'errors': [e for e in events if e.get('type') == 'tool_output' and e.get('error')]}))
    assert executions[0][0] in {'search_emails', 'list_emails'}
    assert all(name in {'search_emails', 'list_emails', 'read_email'} for name, _ in executions)
    if 'Morgan send' in prompt:
        assert '09:00' in answer or '9:00' in answer or '9 AM' in answer
    else:
        assert any(name == 'read_email' and str(args.get('uid')) == '73' for name, args in executions)
        assert '14:45' in answer or '2:45' in answer
    assert chunks[-1] == 'data: [DONE]\n\n'


@pytest.mark.parametrize('previous_answer,prompt', [
    ('Please check your email manually.', 'Search my email again.'),
    ('Morgan sent the email at 09:00 UTC.', 'Not when it was sent. When does it start? Check my email.'),
])
async def test_live_ajax_email_followup_keeps_original_question(monkeypatch, previous_answer, prompt):
    prior = [
        {'role': 'user', 'content': 'When does the design review start? Look in my email.'},
        {'role': 'assistant', 'content': previous_answer},
    ]
    await test_live_ajax_latest_email_is_not_web_discovery(
        monkeypatch, prompt, 'Archive', 'work@example.com', prior=prior)


CASES = [
    ('supplied', [], 'Draft an email to Jon thanking him for lunch yesterday.', 'draft', (), 'lunch'),
    ('missing', [], 'Draft an email to Jon.', 'draft', (), None),
    ('followup', [
        {'role': 'user', 'content': 'Draft an email to Jon.'},
        {'role': 'assistant', 'content': 'What would you like to say to Jon?'},
    ], 'Thanks for lunch yesterday', 'draft', (), 'lunch'),
    ('revision', [
        {'role': 'user', 'content': 'Draft an email thanking Jon for lunch.'},
        {'role': 'assistant', 'content': 'Subject: Thanks\nHi Jon, Thank you for lunch yesterday. It was lovely to catch up. Best wishes.'},
    ], 'Make it more casual and keep it under 30 words.', 'revise', (), 'lunch'),
    ('lookup', [], 'Read email UID 42 in INBOX on account fixture@example.com, then draft a reply to Jon accepting his invitation. Do not send it.', 'draft', ('email',), 'picnic'),
    ('editor', [], 'Write a reply in this email draft accepting the invitation.', 'draft', (), 'saturday'),
    ('cancel', [
        {'role': 'user', 'content': 'Draft an email to Jon.'},
        {'role': 'assistant', 'content': 'What should it say?'},
    ], 'Cancel that email. What is 12 times 3?', 'other', (), '36'),
    ('send', [], 'Send an email to jon@example.com from fixture@example.com with subject Lunch and body Thanks for lunch.', 'send', (), None),
]


@pytest.mark.parametrize('name,prior,prompt,operation,dependencies,expected', CASES, ids=[c[0] for c in CASES])
async def test_live_ajax_email_harness(monkeypatch, name, prior, prompt, operation, dependencies, expected):
    import src.clean_agent_preview as module
    executions = []

    async def execute(block, **kwargs):
        executions.append(block)
        # All mutations and reads are fixture-only, even if unexpectedly chosen.
        if block.tool_type.removeprefix('mcp__email__') == 'read_email':
            return 'read_email', {'exit_code': 0, 'output': 'From: Jon <jon@example.com>\nSubject: Picnic\nWould you like to join our picnic on Saturday at noon?'}
        if block.tool_type == 'update_document':
            return 'update_document', {'exit_code': 0, 'output': 'Document updated',
                'doc_id': 'fixture-draft', 'title': 'Picnic', 'language': 'email',
                'content': block.content, 'version': 2}
        return block.tool_type, {'exit_code': 1, 'error': 'Fixture does not execute this operation; nothing was sent or changed.'}

    monkeypatch.setattr(module, 'execute_tool_block', execute)
    names = {'web_search', 'web_fetch', 'ask_user', 'read_email', 'send_email', 'update_document'}
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'].removeprefix('mcp__email__') in names]
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    editor = SimpleNamespace(id='fixture-draft', title='Picnic', language='email',
        current_content='To: jon@example.com\nSubject: Re: Picnic\n---\nWould you like to join our picnic on Saturday at noon?') if name == 'editor' else None
    chunks = [chunk async for chunk in stream_preview(
        endpoint_url=URL, model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': prompt}],
        history_session=SimpleNamespace(history=prior), active_document=editor,
        session_id='fixture-email-intent', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=4,
    )]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    assert not any(c.startswith('event: error') for c in chunks), chunks
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    scope = next((e for e in events if e.get('stage') == 'email_task_scope'), None)
    answer = next((e['content'] for e in reversed(events) if e.get('type') == 'final_response'), ''.join(e.get('delta', '') for e in events))
    print(json.dumps({'case': name, 'scope': scope, 'answer': answer,
        'executions': [b.tool_type for b in executions], 'classifier': metrics['email_task_scope'],
        'seconds': metrics['response_time']}, ensure_ascii=False))
    assert scope and scope['operation'] == operation
    assert set(scope['dependencies']) == set(dependencies)
    assert 'ask_user' not in scope['offered_tools']
    assert not any(b.tool_type in {'web_search', 'web_fetch', 'send_email', 'mcp__email__send_email'} for b in executions)
    if name == 'editor':
        writes = [b for b in executions if b.tool_type == 'update_document']
        assert len(writes) == 1
        assert expected in writes[0].content.lower()
    elif expected:
        assert expected in answer.lower()
    if name == 'missing':
        assert not executions
        assert 'subject:' not in answer.lower()
        assert any(word in answer.lower() for word in ('what', 'content', 'say', 'about'))
    if name in {'supplied', 'followup', 'revision'}:
        assert not executions
        assert '?' not in answer
    if name == 'lookup':
        assert any(b.tool_type.removeprefix('mcp__email__') == 'read_email' for b in executions)
    assert chunks[-1] == 'data: [DONE]\n\n'
