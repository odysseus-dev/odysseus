import pytest

from src.clean_agent_preview import browser_transport_recovery


URL = 'https://example.com/catalog/'


@pytest.mark.parametrize('error', ['HTTP2_PROTOCOL_ERROR', 'NAME_NOT_RESOLVED', 'CONNECTION_RESET'])
def test_failed_navigation_preserves_task_and_uses_permitted_fetch(error):
    message = browser_transport_recovery(
        {'action': 'open', 'url': URL}, 'net::ERR_' + error, {'web_fetch'}, set())
    assert URL in message
    assert 'original objective' in message
    assert 'Use web_fetch once' in message


def test_exhausted_fetch_uses_search_instead_of_bouncing():
    message = browser_transport_recovery(
        {'action': 'open', 'url': URL}, 'net::ERR_HTTP2_PROTOCOL_ERROR',
        {'web_fetch', 'web_search'}, {URL.rstrip('/')})
    assert 'Use web_search' in message
    assert 'Use web_fetch' not in message


@pytest.mark.parametrize('action', ['click', 'fill', 'press', 'evaluate'])
def test_mutating_batch_never_replayed(action):
    assert not browser_transport_recovery(
        {'action': 'batch', 'commands': [['open', URL], [action, 'target']]},
        'net::ERR_HTTP2_PROTOCOL_ERROR', {'web_fetch'}, set())


def test_read_only_batch_and_no_available_tools():
    message = browser_transport_recovery(
        {'action': 'batch', 'commands': [['open', URL], ['snapshot']]},
        'net::ERR_HTTP2_PROTOCOL_ERROR', set(), set())
    assert 'No permitted retrieval fallback' in message


@pytest.mark.parametrize('output', ['net::ERR_CERT_AUTHORITY_INVALID', 'CAPTCHA', 'Access denied', 'OK'])
def test_no_transport_recovery_for_security_or_success(output):
    assert not browser_transport_recovery({'action': 'open', 'url': URL}, output, {'web_fetch'}, set())


@pytest.mark.asyncio
@pytest.mark.parametrize('fetch_succeeds', [False, True])
async def test_stream_recovers_navigation_then_fetch_without_email_classifier(monkeypatch, fetch_succeeds):
    import json
    from types import SimpleNamespace
    from dataclasses import replace
    import src.clean_agent_preview as module
    import src.email_task_intent as email_intent
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract

    requests, calls = [], []
    def call(name, args):
        return {'tool_calls': [{'index': 0, 'id': name, 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}
    responses = iter([
        call('private_browser', {'action': 'open', 'url': URL}),
        call('web_fetch', {'url': URL}),
        call('web_search', {'query': 'wardrobe'}),
        {'content': 'The site could not be read and no usable product evidence was found.'},
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
            return Response(next(responses))
    async def execute(block, **kwargs):
        calls.append(block.tool_type)
        if block.tool_type == 'private_browser':
            return block.tool_type, {'output': 'net::ERR_HTTP2_PROTOCOL_ERROR', 'exit_code': 1}
        if block.tool_type == 'web_fetch':
            return block.tool_type, {'output': 'Homepage navigation' if fetch_succeeds else 'Connection reset',
                                     'exit_code': 0 if fetch_succeeds else 1}
        assert 'site:example.com' in block.content
        return block.tool_type, {'output': 'No usable product results.', 'exit_code': 0}
    async def no_email_classifier(*args, **kwargs):
        raise AssertionError('Web-only request must not use email interpretation')
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    monkeypatch.setattr(email_intent, 'classify_email_task', no_email_classifier)
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'private_browser', 'web_fetch', 'web_search'}]
    contract = replace(resolve_full_inventory_contract(schemas=schemas, policy=policy),
                       required=frozenset({'private_browser'}))
    chunks = [chunk async for chunk in module.stream_preview(
        endpoint_url='http://fixture', model='Ajax', headers={}, turn_contract=contract,
        history_session=SimpleNamespace(history=[
            {'role': 'user', 'content': 'Browse https://wrong.example/catalog/ and find a wardrobe'},
            {'role': 'assistant', 'content': 'Navigation failed.'}]),
        messages=[{'role': 'user', 'content': 'Browse https://wrong.example/catalog/ and find a wardrobe'},
                  {'role': 'assistant', 'content': 'Navigation failed.'},
                  {'role': 'user', 'content': URL}],
        session_id='fixture-browser', owner='test', disabled_tools=set(), tool_policy=policy)]
    assert calls == ['private_browser', 'web_fetch', 'web_search'], '\n'.join(chunks)
    assert requests[1]['tool_choice'] == 'auto'
    assert [s['function']['name'] for s in requests[1]['tools']] == ['web_fetch']
    assert requests[2]['tool_choice'] == 'auto'
    assert [s['function']['name'] for s in requests[2]['tools']] == ['web_search']
    assert any('browser_transport_fallback' in chunk for chunk in chunks)
    assert any('[DONE]' in chunk for chunk in chunks)
    assert all(m['role'] != 'system' for m in requests[0]['messages'][1:])
    assert 'Corrected target: ' + URL in requests[0]['messages'][0]['content']


@pytest.mark.parametrize('arguments', ['"url"', '[]', 'null', '42'])
def test_provider_history_requires_object_tool_arguments(arguments):
    from src.clean_agent_preview import protocol_safe_tool_calls
    calls = [{'function': {'name': 'web_fetch', 'arguments': arguments}}]
    assert protocol_safe_tool_calls(calls)[0]['function']['arguments'] == '{}'
    assert calls[0]['function']['arguments'] == arguments
