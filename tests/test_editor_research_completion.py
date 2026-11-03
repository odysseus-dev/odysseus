import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src import clean_agent_preview as runner
from tests.test_editor_writing_action_routing import ACTIONS
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import (
    requested_capabilities, selected_tools_for_request,
    preserve_bound_editor_selected_tools, resolve_turn_contract,
)


@pytest.mark.asyncio
async def test_research_does_not_replace_editor_deliverable_with_briefing(monkeypatch):
    prompt = ACTIONS['sources']
    requests = []
    events = []
    calls = []
    source = 'Water freezes at 10 degrees Celsius.'

    @asynccontextmanager
    async def response(*args, **kwargs):
        request = args[3]
        requests.append(request)
        step = len(requests)
        if step <= 2:
            name = 'web_search' if step == 1 else 'suggest_document'
            arguments = {'query': 'water freezing point'} if step == 1 else {
                'suggestions': [{'find': source, 'replace': 'Water freezes at 0 degrees Celsius. https://example.com/water', 'reason': 'Correct the claim.'}],
            }
            delta = {'tool_calls': [{'index': 0, 'id': f'call_{step}', 'type': 'function',
                                    'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
        else:
            delta = {'content': 'Suggestions are queued for review.'}

        class Response:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: ' + json.dumps({'choices': [{'delta': delta}]})
                yield 'data: [DONE]'

        yield Response()

    async def execute(block, **kwargs):
        calls.append(block.tool_type)
        result = {'results': [{'url': 'https://example.com/water', 'content': 'Water freezes at 0 degrees Celsius.'}]} if block.tool_type == 'web_search' else {
            'action': 'suggest', 'count': 1, 'finds': [source], 'instruction': 'Suggestions are queued for review.',
        }
        return block.tool_type, {'output': json.dumps(result), 'exit_code': 0}

    monkeypatch.setattr(runner, 'preview_model_response', response)
    monkeypatch.setattr(runner, 'execute_tool_block', execute)
    policy = ToolPolicy()
    selected = preserve_bound_editor_selected_tools(prompt, selected_tools_for_request(prompt), active_document=True)
    contract = resolve_turn_contract(capabilities=requested_capabilities(prompt, active_document=True),
        schemas=FUNCTION_TOOL_SCHEMAS, policy=policy, selected_tools=selected)
    async for chunk in runner.stream_preview(
        endpoint_url='http://fixture', model='Ajax', headers={},
        messages=[{'role': 'user', 'content': prompt}], turn_contract=contract,
        session_id='fixture-editor-sources', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=4,
        active_document=SimpleNamespace(id='fixture', title='Essay', language='markdown', current_content=source),
    ):
        if chunk.startswith('data: ') and '[DONE]' not in chunk:
            events.append(json.loads(chunk[6:]))
    assert calls == ['web_search', 'suggest_document']
    assert 'suggest_document' in {s['function']['name'] for s in requests[1]['tools']}
    assert not any(e.get('reason') in {'research_before_synthesis', 'incomplete_research_answer', 'requested_source_link_missing'} for e in events)
