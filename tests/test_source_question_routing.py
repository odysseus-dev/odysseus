"""Source-dependent answers must not bypass retrieval or leak discarded guesses."""
import json

import pytest

from src.email_task_intent import EmailTaskIntent, scope_email_tools
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract


def schema(name):
    return {'type': 'function', 'function': {'name': name, 'description': name,
            'parameters': {'type': 'object', 'properties': {}}}}


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('uid', [42, '42'])
def test_structured_message_identifiers_require_successful_evidence(failed, uid):
    from src.clean_agent_preview import email_identifier_error
    history = [
        {'role': 'assistant', 'tool_calls': [{'id': 'lookup', 'function': {
            'name': 'search_emails', 'arguments': '{"query":"keys"}'}}]},
        {'role': 'tool', 'tool_call_id': 'lookup', 'content': json.dumps({
            'exit_code': 1 if failed else 0,
            'results': [{'uid': uid, 'subject': 'Key return'}],
        })},
    ]
    assert bool(email_identifier_error('read_email', {'uid': '42'}, history=history)) == failed
    assert email_identifier_error('read_email', {'uid': '99'}, history=history)


@pytest.mark.parametrize('prompt', ['Look email, when is my dhl arriving',
    'Look through my inbox for the delivery date', 'Look at my notes for the address'])
def test_look_in_named_source_does_not_fall_back_to_files(prompt):
    from src.turn_contract import _clause_capabilities
    assert _clause_capabilities(prompt) == ({'notes'} if 'notes' in prompt else {'email'})


def test_read_scope_only_narrows_existing_tools():
    intent = EmailTaskIntent('read', ('email',), 'Answer from inbox')
    tools = [schema(n) for n in ['web_search', 'read_email', 'send_email', 'list_email_accounts']]
    assert scope_email_tools(tools, intent) == [tools[1], tools[3]]
    assert scope_email_tools([], intent) == []


@pytest.mark.parametrize('disabled', [False, True])
def test_search_inventory_keeps_permission_filtered_read_continuation(disabled):
    from src.turn_contract import resolve_turn_contract
    tools = [schema(n) for n in ['search_emails', 'read_email', 'download_attachment',
                                  'list_email_accounts', 'send_email', 'bash']]
    policy = ToolPolicy(disabled_tools=frozenset({'read_email'}) if disabled else frozenset())
    contract = resolve_turn_contract(capabilities={'email'}, schemas=tools, policy=policy,
                                     selected_tools={'search_emails'})
    assert ('read_email' in contract.offered) is not disabled
    assert 'download_attachment' in contract.offered
    assert 'send_email' not in contract.offered
    assert 'bash' not in contract.offered


@pytest.mark.asyncio
@pytest.mark.parametrize('thinking', ['off', 'on'])
@pytest.mark.parametrize('mode', ['ignores', 'retrieves', 'fails', 'accounts_only', 'unavailable',
                                'search_then_read', 'list_then_read', 'search_json', 'list_json',
                                'search_skips_read', 'list_skips_read',
                                'search_metadata', 'list_metadata', 'search_empty', 'list_empty'])
async def test_source_question_requires_lookup_before_visible_answer(monkeypatch, thinking, mode):
    import src.clean_agent_preview as module
    import src.email_task_intent as intent_module
    requests = []
    calls = []
    lookup = ('list_emails' if mode.startswith('list_') else 'search_emails')
    uses_lookup = mode.startswith(('search_', 'list_'))
    needs_body = uses_lookup and not mode.endswith('metadata')
    reads_body = needs_body and not mode.endswith(('skips_read', 'empty'))
    lookup_only = mode.endswith(('metadata', 'empty'))

    async def classify(*args, **kwargs):
        return EmailTaskIntent('read', ('documents',) if mode == 'unavailable' else ('email',),
                               'Find the drop-off address in the requested source',
                               needs_clarification=True, requires_content=needs_body)

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
            request = kwargs['json']
            requests.append(request)
            if (len(requests) == 1 and mode != 'ignores') or (reads_body and len(requests) == 2):
                name = (lookup if uses_lookup and len(requests) == 1
                        else 'list_email_accounts' if mode == 'accounts_only' else 'read_email')
                return Response({'tool_calls': [{'index': 0, 'id': 'lookup', 'type': 'function',
                                     'function': {'name': name, 'arguments': '{"query":"keys"}' if name == 'search_emails' else '{}'}}]})
            if lookup_only:
                return Response({'content': 'No matching email.' if mode.endswith('empty') else 'Subject: Key return'})
            return Response({'content': 'Use the mailbox.' if mode != 'retrieves' and not reads_body
                             else 'The email says 12 Example Street.'})

    async def execute(block, *args, **kwargs):
        calls.append(block)
        if uses_lookup and block.tool_type.removeprefix('mcp__email__') == lookup:
            if mode.endswith('empty'):
                return 'fixture lookup', {'results': [], 'exit_code': 0}
            if mode.endswith('json'):
                return 'fixture lookup', {'results': [{'uid': 42, 'subject': 'Key return'}], 'exit_code': 0}
            return 'fixture lookup', {'output': 'UID: 42\nSubject: Key return', 'exit_code': 0}
        return 'fixture lookup', {'output': '12 Example Street' if mode == 'retrieves' or reads_body else 'Lookup unavailable',
                'exit_code': 1 if mode == 'fails' else 0}

    monkeypatch.setattr(intent_module, 'classify_email_task', classify)
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(
        schemas=[schema('read_email'), schema('list_email_accounts'), schema('web_search')]
        + ([{'type': 'function', 'function': {'name': lookup, 'description': 'Find mail',
              'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}}]
           if uses_lookup else []), policy=policy)
    events = [json.loads(chunk[6:]) async for chunk in module.stream_preview(
        endpoint_url='http://test', model='Ajax', headers={}, turn_contract=contract,
        messages=[{'role': 'user', 'content': 'in my email what address should I drop off the keys at'}],
        session_id='test', owner='test', disabled_tools=set(), tool_policy=policy,
        thinking_mode=thinking,
    ) if '[DONE]' not in chunk]
    if mode == 'unavailable':
        assert requests == []
        assert not calls
        assert any('could not retrieve' in e.get('content', '') for e in events)
        return
    # Ajax's adapter uses auto decoding; evidence requirements remain enforced
    # by the harness, including when the model ignores the offered tools.
    assert requests[0]['tool_choice'] == 'auto'
    system_text = '\n'.join(m.get('content', '') for m in requests[0]['messages']
                            if m.get('role') == 'system')
    assert 'Find the drop-off address in the requested source' not in system_text
    assert any(m.get('role') == 'user' and
               'in my email what address should I drop off the keys at' in m.get('content', '')
               for m in requests[0]['messages'])
    assert {s['function']['name'] for s in requests[0]['tools']} == (
        {'read_email', 'list_email_accounts'} | ({lookup} if uses_lookup else set()))
    if needs_body and not mode.endswith('empty'):
        assert {s['function']['name'] for s in requests[1]['tools']} == {'read_email'}, [e for e in events if e.get('type') == 'tool_output']
        assert requests[1]['tool_choice'] == 'auto'
    visible = ' '.join(e.get('delta', '') + (e.get('content', '') if e.get('type') == 'final_response' else '')
                       for e in events)
    assert 'Use the mailbox' not in visible
    if lookup_only:
        assert len(calls) == 1
        assert len(requests) == 2
        assert 'No matching email.' in visible if mode.endswith('empty') else 'Subject: Key return' in visible
    elif mode == 'retrieves' or reads_body:
        assert calls
        assert '12 Example Street' in visible
    else:
        assert 'could not retrieve' in visible
        assert len(requests) <= 3
    metrics = next(e['data'] for e in events if e.get('type') == 'metrics')
    assert metrics['email_task_scope']['operation'] == 'read'
    assert metrics['email_task_scope']['dependencies'] == ['email']
