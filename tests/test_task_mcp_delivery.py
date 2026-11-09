"""Scheduled MCP delivery must use the task owner's mailbox and report failure."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.task_scheduler import TaskScheduler


@pytest.mark.asyncio
async def test_mcp_delivery_uses_task_owner_mailbox(monkeypatch):
    calls = []
    monkeypatch.setattr('routes.email_helpers._get_email_config',
                        lambda **kwargs: calls.append(kwargs) or {'from_address': 'alice@example.test'})
    manager = SimpleNamespace(call_tool=AsyncMock(return_value={'exit_code': 0, 'stdout': 'Sent email to alice@example.test'}))
    monkeypatch.setattr('src.tool_utils.get_mcp_manager', lambda: manager)
    task = SimpleNamespace(id='task', name='Digest', owner='alice')
    await TaskScheduler(None)._deliver_via_mcp('mcp__email__send_email', task, 'content')
    assert calls == [{'owner': 'alice'}]
    assert manager.call_tool.await_args.args[1]['to'] == 'alice@example.test'
    assert manager.call_tool.await_args.args[1]['_odysseus_owner'] == 'alice'


@pytest.mark.asyncio
@pytest.mark.parametrize('output', [
    {'exit_code': 1, 'stderr': 'failed'},
    {'error': 'not connected', 'exit_code': 1},
    {'exit_code': 0, 'stdout': 'Error: SMTP failed'},
    {'exit_code': 0, 'stdout': 'Draft staged for approval. Nothing has been sent yet.'},
    {},
    None,
])
async def test_mcp_delivery_failure_is_reported(monkeypatch, output):
    monkeypatch.setattr('routes.email_helpers._get_email_config', lambda **kwargs: {})
    manager = SimpleNamespace(call_tool=AsyncMock(return_value=output))
    monkeypatch.setattr('src.tool_utils.get_mcp_manager', lambda: manager)
    task = SimpleNamespace(id='task', name='Digest', owner='alice@example.test')
    with pytest.raises(RuntimeError):
        await TaskScheduler(None)._deliver_via_mcp('mcp__email__send_email', task, 'content')


@pytest.mark.asyncio
async def test_generic_mcp_delivery_reports_text_errors(monkeypatch):
    monkeypatch.setattr('routes.email_helpers._get_email_config', lambda **kwargs: {})
    manager = SimpleNamespace(call_tool=AsyncMock(return_value={'exit_code': 0, 'stdout': 'Error: delivery failed'}))
    monkeypatch.setattr('src.tool_utils.get_mcp_manager', lambda: manager)
    task = SimpleNamespace(id='task', name='Digest', owner='alice@example.test')
    with pytest.raises(RuntimeError):
        await TaskScheduler(None)._deliver_via_mcp('mcp__custom__send', task, 'content')


@pytest.mark.asyncio
async def test_authenticated_ownerless_action_is_not_executed(monkeypatch):
    from src import builtin_actions
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    action = AsyncMock(return_value=('done', True))
    monkeypatch.setitem(builtin_actions.BUILTIN_ACTIONS, 'test_action', action)
    task = SimpleNamespace(id='task', name='job', owner=None, action='test_action',
                           prompt='', task_type='action')
    with pytest.raises(RuntimeError):
        await TaskScheduler(None)._execute_action(task)
    action.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticated_ownerless_mcp_delivery_is_not_executed(monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    manager = SimpleNamespace(call_tool=AsyncMock())
    monkeypatch.setattr('src.tool_utils.get_mcp_manager', lambda: manager)
    task = SimpleNamespace(id='task', name='job', owner=None)
    with pytest.raises(RuntimeError):
        await TaskScheduler(None)._deliver_via_mcp('mcp__email__send_email', task, 'content')
    manager.call_tool.assert_not_awaited()
