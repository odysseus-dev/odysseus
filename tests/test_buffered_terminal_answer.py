import json
from contextlib import asynccontextmanager

import pytest

from src import clean_agent_preview as runner
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import requested_capabilities, selected_tools_for_request, resolve_turn_contract


@pytest.mark.asyncio
async def test_accepted_text_only_answer_is_emitted_after_required_tool_buffering(monkeypatch):
    prompt = 'What time would that event start if it were pushed back by two hours? Do not change it.'
    answer = 'It would start at 16:00 UTC.'

    class Response:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': answer}}]})
            yield 'data: [DONE]'

    @asynccontextmanager
    async def response(*args, **kwargs):
        yield Response()

    monkeypatch.setattr(runner, 'preview_model_response', response)
    monkeypatch.setattr(runner, 'required_read_tool_choice', lambda *args, **kwargs: 'required')
    policy = ToolPolicy()
    selected = selected_tools_for_request(prompt)
    contract = resolve_turn_contract(
        capabilities=requested_capabilities(prompt), schemas=FUNCTION_TOOL_SCHEMAS,
        policy=policy, selected_tools=selected, required_tools=selected or (), message=prompt,
    )
    events = []
    async for chunk in runner.stream_preview(
        endpoint_url='http://fixture', model='Ajax', headers={},
        messages=[{'role': 'user', 'content': prompt}], turn_contract=contract,
        session_id='fixture-buffered-answer', owner='fixture', disabled_tools=set(),
        tool_policy=policy, thinking_mode='off', max_rounds=2,
    ):
        if chunk.startswith('data: ') and '[DONE]' not in chunk:
            events.append(json.loads(chunk[6:]))
    finals = [e['content'] for e in events if e.get('type') == 'final_response']
    assert finals == [answer]
    assert not any(e.get('delta') for e in events)
    assert not any(e.get('type') == 'tool_start' for e in events)
