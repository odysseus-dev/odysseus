"""Execution outcomes must reach the model, not just the tool-event UI."""
import json

import pytest

from src.agent_tools.subprocess_tools import BashTool
from src.clean_agent_preview import preview_tool_result_text


@pytest.mark.asyncio
async def test_failed_shell_retains_exit_status_and_both_streams_for_followup(tmp_path):
    from tests.process_resource_helpers import launch_authority
    cmd = "printf 'PHASE_ONE_DONE\\n'; printf 'CHECK_FAILED\\n' >&2; exit 7"
    with launch_authority(cmd, tmp_path, session_id="chat"):
        result = await BashTool().execute(cmd, {"session_id": "chat"})
    assert result['exit_code'] == 7
    observed = preview_tool_result_text(result, 'bash', {})
    assert 'PHASE_ONE_DONE' in observed and 'CHECK_FAILED' in observed
    assert json.loads(observed)['exit_code'] == 7


def test_partial_output_does_not_hide_error_or_timeout_evidence():
    result = {'output': 'partial data', 'error': 'Timed out', 'exit_code': 124,
              'stdout': 'first chunk', 'stderr': 'diagnostic'}
    observed = json.loads(preview_tool_result_text(result, 'bash', {}))
    assert observed == result


def test_failure_status_survives_observation_truncation():
    observed = preview_tool_result_text({'output': 'x' * 10000, 'exit_code': 9, 'error': 'Incomplete scan'}, 'grep', {})
    assert observed.startswith('{"exit_code": 9, "error": "Incomplete scan"')
    assert 'truncated' in observed
    assert len(observed) < 8100


def test_successful_plain_output_keeps_existing_compact_representation():
    assert preview_tool_result_text({'output': 'alpha', 'exit_code': 0}, 'read_file', {}) == 'alpha'


@pytest.mark.asyncio
async def test_unoffered_proposal_is_marked_blocked_not_an_execution_attempt(monkeypatch):
    import src.clean_agent_preview as preview
    from src.tool_policy import ToolPolicy
    from src.turn_contract import resolve_full_inventory_contract
    responses = iter([
        {'tool_calls': [{'index': 0, 'id': 'unoffered', 'function': {'name': 'manage_skills',
            'arguments': json.dumps({'action': 'view', 'name': 'not-executed'})}}]},
        {'content': 'That tool is unavailable.'},
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
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=[], policy=policy)
    raw = [chunk async for chunk in preview.stream_preview(endpoint_url='http://test', model='test',
        messages=[{'role': 'user', 'content': 'Show that skill'}], headers={}, turn_contract=contract,
        session_id='unoffered-test', owner='test', disabled_tools=set(), tool_policy=policy)]
    events = [json.loads(chunk[6:]) for chunk in raw if '[DONE]' not in chunk]
    assert not any(e.get('type') == 'tool_start' for e in events)
    output = next(e for e in events if e.get('type') == 'tool_output')
    assert output['execution_attempted'] is False
    assert output['blocked'] is True
    saved = next(e['data']['tool_events'] for e in events if e.get('type') == 'metrics')
    assert saved == [output]
