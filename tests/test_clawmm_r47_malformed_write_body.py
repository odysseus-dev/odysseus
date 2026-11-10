import json

import pytest

from src.clean_agent_preview import stream_preview
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract


@pytest.mark.asyncio
async def test_malformed_text_artifact_write_uses_one_bounded_raw_body_handoff(monkeypatch):
    import src.clean_agent_preview as module

    requests = []
    responses = iter([
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'truncated-write', 'function': {
                'name': 'write_file',
                'arguments': '{"content": "# Evidence\\n\\nA long report that was clipped',
            },
        }]}}]},
        {'choices': [{'delta': {
            'content': '# Evidence\n\nComplete recovered report.',
        }}]},
    ])

    class Response:
        def __init__(self, payload):
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))

    executed = []

    async def execute(block, **kwargs):
        executed.append(block)
        return 'write_file', {'output': 'Wrote output.html', 'exit_code': 0}

    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(module, 'execute_tool_block', execute)
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item['function']['name'] == 'write_file'
    )
    contract = resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy())

    raw = [chunk async for chunk in stream_preview(
        endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': (
            "Save a concise evidence-grounded Markdown report to 'output.md' "
            'with write_file and verify it with read_file.'
        )}],
        headers={}, turn_contract=contract, session_id='test', owner='test',
        disabled_tools=set(), tool_policy=ToolPolicy(), workspace='/tmp/workspace',
        client_runtime_context={
            'surface': 'odysseus-native', 'terminal_agent': True,
            'unattended_mode': True,
        }, max_tokens=8192, max_rounds=4,
    )]

    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert len(requests) == 2
    assert 'tools' not in requests[1]
    assert requests[1]['max_tokens'] == 8192
    assert len(executed) == 1
    assert executed[0].tool_type == 'write_file'
    assert executed[0].content == (
        'output.md\n'
        '# Evidence\n\nComplete recovered report.'
    )
    assert any(event.get('type') == 'artifact_body_handoff' for event in events)
    assert any(
        event.get('type') == 'tool_output'
        and event.get('tool') == 'write_file'
        and not event.get('error')
        for event in events
    )
