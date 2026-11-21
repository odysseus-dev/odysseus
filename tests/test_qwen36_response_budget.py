import json

import pytest

from src.clean_agent_preview import preview_response_token_budget, stream_preview
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract


@pytest.mark.asyncio
async def test_qwen36_preview_sends_enough_budget_for_reasoning_and_answer(monkeypatch):
    import src.clean_agent_preview as preview

    requests = []

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'reasoning_content': 'reasoning'}}]})
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'A complete answer.'}}]})
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response()

    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    events = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='tinyrick/Qwen3.6-35B-A3B:Q4_K_M',
        messages=[{'role': 'user', 'content': 'Explain a harmless concept.'}], headers={},
        turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), max_tokens=32768,
    )]

    assert requests[0]['max_tokens'] == 4096
    assert any('A complete answer.' in event for event in events)
    assert not any('test model returned no answer' in event for event in events)


def test_budget_respects_smaller_configured_limit_and_other_models():
    assert preview_response_token_budget('Qwen3.6-35B', 512) == 512
    assert preview_response_token_budget('llama3.1:8b', 32768) == 768
