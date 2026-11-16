"""Real manager dispatch with explicitly fake MCP peer responses; no production calls."""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def setup(monkeypatch):
    from src import native_mcp_activity as activity
    registry = activity.Registry()
    monkeypatch.setattr(activity, 'REGISTRY', registry)
    from src.mcp_manager import McpManager
    return registry, McpManager()


@pytest.mark.asyncio
@pytest.mark.parametrize('server', ['image_gen', 'memory', 'rag', 'email', 'builtin_browser'])
async def test_all_native_servers_count_and_fence_success(setup, server):
    registry, manager = setup
    entered, release = asyncio.Event(), asyncio.Event()
    class Peer:
        async def call_tool(self, name, arguments):
            entered.set()
            await release.wait()
            return SimpleNamespace(content=[], isError=False)
    manager._sessions[server] = Peer()
    task = asyncio.create_task(manager.call_tool(f'mcp__{server}__fixture', {}))
    await asyncio.wait_for(entered.wait(), 2)
    assert registry.snapshot()['in_flight'] == 1
    registry.fence()
    refused = await manager.call_tool(f'mcp__{server}__fixture', {})
    assert refused['failure_kind'] == 'restart_draining'
    assert registry.snapshot()['in_flight'] == 1
    release.set()
    assert (await task)['exit_code'] == 0
    assert registry.snapshot()['in_flight'] == 0
    assert registry.snapshot()['unknown'] == 0
    assert registry.snapshot()['draining'] is True


@pytest.mark.asyncio
async def test_cancelled_remote_call_is_not_falsely_idle(setup):
    registry, manager = setup
    entered = asyncio.Event()
    class Peer:
        async def call_tool(self, *args):
            entered.set()
            await asyncio.Event().wait()
    manager._sessions['memory'] = Peer()
    task = asyncio.create_task(manager.call_tool('mcp__memory__fixture', {}))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry.snapshot()['in_flight'] == 0
    assert registry.snapshot()['unknown'] == 1


@pytest.mark.asyncio
async def test_browser_timeout_retains_unknown_completion(setup, monkeypatch):
    import src.mcp_manager as module
    registry, manager = setup
    monkeypatch.setattr(module, 'browser_mcp_call_timeout', lambda: .02)
    class Peer:
        async def call_tool(self, *args):
            await asyncio.Event().wait()
    manager._sessions['builtin_browser'] = Peer()
    result = await manager.call_tool('mcp__builtin_browser__fixture', {})
    assert result['exit_code'] == 1
    assert registry.snapshot()['in_flight'] == 0
    assert registry.snapshot()['unknown'] == 1


@pytest.mark.asyncio
async def test_logical_error_response_finishes_known_operation(setup):
    registry, manager = setup
    class Peer:
        async def call_tool(self, *args):
            return SimpleNamespace(content=[], isError=True)
    manager._sessions['email'] = Peer()
    assert (await manager.call_tool('mcp__email__fixture', {}))['exit_code'] == 1
    assert registry.snapshot()['in_flight'] == registry.snapshot()['unknown'] == 0


@pytest.mark.asyncio
async def test_sdk_live_process_cleanup_never_escalates(setup):
    from src.native_mcp_stdio import OwnedProcess
    registry, manager = setup
    class Process:
        pid = 0
        returncode = None
        async def aclose(self):
            raise AssertionError('Live SDK process close can kill; must never be called')
    proxy = OwnedProcess(Process(), registry, manager._activity_owner, 'fixture')
    await proxy.__aexit__(None, None, None)
    assert registry.snapshot()['unknown'] > 0


@pytest.mark.asyncio
async def test_bounded_drain_does_not_cancel_existing_call(setup):
    registry, manager = setup
    entered, release = asyncio.Event(), asyncio.Event()
    class Peer:
        async def call_tool(self, *args):
            entered.set()
            await release.wait()
            return SimpleNamespace(content=[], isError=False)
    manager._sessions['rag'] = Peer()
    task = asyncio.create_task(manager.call_tool('mcp__rag__fixture', {}))
    await asyncio.wait_for(entered.wait(), 2)
    registry.fence()
    result = await registry.wait_idle(.02)
    assert result['state'] == 'mcp-drain-timeout'
    assert not task.done(), 'Bounded observation must not cancel existing MCP work'
    release.set()
    await task
    assert (await registry.wait_idle(.2))['ok'] is True


@pytest.mark.asyncio
async def test_coordinator_waits_for_accepted_call_before_committing_shutdown(setup, monkeypatch):
    from src import windows_app_restart as control
    registry, manager = setup
    monkeypatch.setattr(control, 'full_guard', lambda *args, **kwargs: {'clear': True, 'errors': []})
    monkeypatch.setattr(control, '_commit_shutdown', lambda server: setattr(server, 'should_exit', True))
    entered, release = asyncio.Event(), asyncio.Event()
    class Peer:
        async def call_tool(self, *args):
            entered.set()
            await release.wait()
            return SimpleNamespace(content=[], isError=False)
    manager._sessions['rag'] = Peer()
    operation = asyncio.create_task(manager.call_tool('mcp__rag__fixture', {}))
    await asyncio.wait_for(entered.wait(), 2)
    gate = control.Admission(None)
    server = SimpleNamespace(should_exit=False)
    stopping = asyncio.create_task(control.prepare_shutdown(server, gate, {}, {}, [], timeout=2))
    for _ in range(100):
        if registry.snapshot()['draining']:
            break
        await asyncio.sleep(.01)
    assert registry.snapshot()['draining'] and not server.should_exit
    release.set()
    await operation
    assert (await stopping)['ok'] is True
    assert server.should_exit is True


@pytest.mark.asyncio
async def test_background_http_connection_stays_counted_after_outer_wait(setup, monkeypatch):
    registry, manager = setup
    entered, release = asyncio.Event(), asyncio.Event()
    async def connect(*args):
        entered.set()
        await release.wait()
        return True
    monkeypatch.setattr(manager, '_connect_http_impl', connect, raising=False)
    task = asyncio.create_task(manager._connect_http('fixture-http', 'fixture', 'http://127.0.0.1:1'))
    await asyncio.wait_for(entered.wait(), .5)
    assert registry.snapshot()['in_flight'] == 1
    registry.fence()
    assert await manager.connect_server('blocked', 'blocked', 'http') is False
    release.set()
    assert await task is True
    assert registry.snapshot()['in_flight'] == 0


@pytest.mark.asyncio
async def test_untracked_low_level_dispatch_never_contacts_peer(setup):
    registry, manager = setup
    class Peer:
        async def call_tool(self, *args):
            raise AssertionError('Untracked dispatch contacted peer')
    with pytest.raises(RuntimeError, match='Untracked'):
        await manager._do_call(Peer(), 'fixture', {})
    assert registry.snapshot()['unknown'] == 1
    assert (await registry.wait_idle(.1))['ok'] is False


def test_stdio_child_pid_reuse_does_not_replace_original_incarnation(setup, monkeypatch):
    from src import windows_restart_os as win
    registry, manager = setup
    root, old_child = {'pid': 10, 'start_token': 'win32:10'}, {'pid': 11, 'start_token': 'win32:11'}
    row = {'identity': root, 'children': {11: old_child}, 'closed': False}
    registry.processes[(manager._activity_owner, 'fixture', 10, 'win32:10')] = row
    monkeypatch.setattr(win, 'process_parents', lambda: {11: 10})
    monkeypatch.setattr(win, 'identity', lambda pid: root if pid == 10 else {'pid': 11, 'start_token': 'win32:99'})
    assert 11 not in registry.accounted_process_ids()
    assert row['children'][11] == old_child
    assert registry.snapshot()['unknown'] > 0


@pytest.mark.asyncio
async def test_expired_control_deadline_cannot_commit_late_shutdown(setup, monkeypatch):
    import time
    from src import windows_app_restart as control
    registry, manager = setup
    def forbidden_guard(*args, **kwargs):
        raise AssertionError('Expired request must abort before preparation')
    monkeypatch.setattr(control, 'full_guard', forbidden_guard)
    server, gate = SimpleNamespace(should_exit=False), control.Admission(None)
    result = await control.prepare_shutdown(server, gate, {}, {}, [], timeout=2,
                                            deadline=time.monotonic() - 1)
    assert not result['ok'] and not server.should_exit
    assert not registry.snapshot()['draining'] and not gate.preparing


@pytest.mark.asyncio
async def test_deadline_expiring_during_final_snapshot_refuses_commit(setup, monkeypatch):
    from src import windows_app_restart as control
    registry, manager = setup
    now, checks = [1.0], [0]
    def guard(*args, **kwargs):
        checks[0] += 1
        return {'clear': True, 'errors': []}
    original = registry.snapshot
    def delayed_snapshot():
        result = original()
        if checks[0] == 3:
            now[0] = 100.0
        return result
    monkeypatch.setattr(control, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(control, 'full_guard', guard)
    monkeypatch.setattr(control, '_commit_shutdown', lambda server: setattr(server, 'should_exit', True))
    monkeypatch.setattr(registry, 'snapshot', delayed_snapshot)
    server = SimpleNamespace(should_exit=False)
    result = await control.prepare_shutdown(server, control.Admission(None), {}, {}, [], timeout=2)
    assert not result['ok'] and not server.should_exit
