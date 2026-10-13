import json
from dataclasses import replace
import pytest


@pytest.mark.asyncio
async def test_empty_followups_do_not_disable_reading_discovered_sources(monkeypatch):
    import src.clean_agent_preview as runtime
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_policy import ToolPolicy
    from src.turn_contract import resolve_full_inventory_contract
    calls = [('web_search', {'query': q}) for q in ['storage research', 'sodium life cycles', 'cold climate chemistry']]
    calls.append(('web_fetch', {'url': 'https://example.org/report'}))
    packets = iter([
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': str(i), 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}
        for i, (name, args) in enumerate(calls)
    ] + [{'choices': [{'delta': {'content': 'Read the report and found the requested evidence.'}}]}])
    requests = []
    class Response:
        def __init__(self, packet): self.packet = packet
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.packet)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(packets))
    executed = []
    async def execute(block, **kwargs):
        executed.append(block.tool_type)
        if len(executed) == 1:
            return block.tool_type, {'output': '[1] Storage report\n    https://example.org/report', 'exit_code': 0, 'evidence_status': 'available'}
        if block.tool_type == 'web_search':
            return block.tool_type, {'output': 'No results', 'exit_code': 0, 'evidence_status': 'empty'}
        return block.tool_type, {'output': 'Report evidence is readable.', 'exit_code': 0}
    monkeypatch.setattr(runtime.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(runtime, 'execute_tool_block', execute)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'web_search', 'web_fetch'}]
    contract = replace(resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy()), routing_experiment='recent_model_choice')
    raw = [x async for x in runtime.stream_preview(endpoint_url='http://test', model='test', headers={},
        messages=[{'role': 'user', 'content': 'Research storage technology.'}], turn_contract=contract,
        session_id='test', owner='test', disabled_tools=set(), tool_policy=ToolPolicy(), max_rounds=5)]
    assert executed == ['web_search', 'web_search', 'web_search', 'web_fetch']
    assert [s['function']['name'] for s in requests[3]['tools']] == ['web_fetch']
    assert any('Previously discovered source URLs remain available' in str(m.get('content')) for m in requests[3]['messages'])
    assert any('Read the report' in x for x in raw)
