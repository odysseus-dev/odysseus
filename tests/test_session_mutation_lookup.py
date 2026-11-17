from types import SimpleNamespace

import pytest

from src.clean_agent_preview import dependent_write_prerequisite_error, required_read_tool_choice
from src.turn_contract import selected_tools_for_request


@pytest.mark.parametrize('prompt', [
    'delete my last chat', 'remove my previous conversation',
    'archive my last session', 'rename my last chat',
])
def test_session_mutations_include_lookup(prompt):
    assert selected_tools_for_request(prompt) == {'list_sessions', 'manage_session'}


def contract():
    return SimpleNamespace(required={'list_sessions', 'manage_session'},
                           required_read_operation=None, permits=lambda name: True)


def test_lookup_precedes_mutation_and_clarification_remains_possible():
    tools = [{'function': {'name': name}} for name in ('manage_session', 'list_sessions')]
    assert required_read_tool_choice(contract(), tools) == {
        'type': 'function', 'function': {'name': 'list_sessions'}}
    assert required_read_tool_choice(contract(), tools, calls=1,
                                    attempted_required_tools={'list_sessions'}) is None


def test_failed_or_missing_lookup_cannot_unlock_mutation():
    assert dependent_write_prerequisite_error(contract(), 'manage_session', set())
    assert dependent_write_prerequisite_error(contract(), 'manage_session', {'web_search'})
    assert dependent_write_prerequisite_error(contract(), 'manage_session', {'list_sessions'}) is None


def test_unavailable_lookup_does_not_force_delete():
    assert required_read_tool_choice(contract(), [{'function': {'name': 'manage_session'}}]) is None
    assert dependent_write_prerequisite_error(contract(), 'manage_session', set())


@pytest.mark.asyncio
async def test_stream_orders_lookup_before_mutation_without_touching_real_chats(monkeypatch):
    import json
    from dataclasses import replace
    import src.clean_agent_preview as module
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract

    calls, requests = [], []
    def tool(name, args):
        return {'tool_calls': [{'index': 0, 'id': name, 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}
    responses = iter([tool('list_sessions', {}),
                      tool('manage_session', {'action': 'delete', 'session_id': 'fixture-previous'}),
                      {'content': 'Deleted the previous fixture chat.'}])
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
        result = ('fixture-current: current chat; fixture-previous: previous chat'
                  if block.tool_type == 'list_sessions' else 'Deleted fixture-previous')
        return block.tool_type, {'results': result, 'exit_code': 0}
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS
               if s['function']['name'] in {'list_sessions', 'manage_session'}]
    scoped = replace(resolve_full_inventory_contract(schemas=schemas, policy=policy),
                     required=frozenset({'list_sessions', 'manage_session'}),
                     active_capabilities=frozenset({'sessions'}),
                     capabilities=frozenset({'sessions'}))
    chunks = [chunk async for chunk in module.stream_preview(
        endpoint_url='http://fixture', model='test', headers={}, turn_contract=scoped,
        messages=[{'role': 'user', 'content': 'delete my last chat'}],
        session_id='fixture-current', owner='test', disabled_tools=set(), tool_policy=policy)]
    assert calls == ['list_sessions', 'manage_session'], '\n'.join(chunks)
    assert requests[0]['tool_choice']['function']['name'] == 'list_sessions'
    assert requests[1].get('tool_choice') is None
    assert any('[DONE]' in chunk for chunk in chunks)
