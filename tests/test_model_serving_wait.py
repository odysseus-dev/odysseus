"""Bounded model startup observation uses real tiny HTTP/process fixtures."""
import pytest
from tests.test_model_serving_control import controlled_runtime, plan_for


@pytest.mark.asyncio
async def test_wait_returns_real_readiness_without_claiming_listener_ownership(controlled_runtime, tmp_path):
    from src.model_serving_status import wait_for_managed_runtime
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    result = await wait_for_managed_runtime(manager.state_path, manager.log_dir, sid, timeout=3)
    assert result['ok'] is True
    assert result['state'] == 'ready'
    assert result['models'] == ['fixture-only-not-a-model']
    assert result['endpoint_ownership_verified'] is False
    assert children[0].poll() is None


@pytest.mark.asyncio
async def test_wait_timeout_leaves_loading_process_running(controlled_runtime, tmp_path, monkeypatch):
    from src.model_serving_status import wait_for_managed_runtime
    control, manager, children = controlled_runtime
    monkeypatch.setenv('ODYSSEUS_SERVING_FIXTURE_LOADING', '1')
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    result = await wait_for_managed_runtime(manager.state_path, manager.log_dir, sid, timeout=.6)
    assert result['ok'] is False
    assert result['state'] == 'startup-timeout'
    assert result['forced'] is False
    assert children[0].poll() is None


@pytest.mark.asyncio
async def test_wait_checks_crash_before_any_http_probe(controlled_runtime, tmp_path, monkeypatch):
    from src import model_serving_status as serving
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    children[0].terminate()
    children[0].wait(timeout=5)
    async def forbidden(*args, **kwargs):
        raise AssertionError('A dead owned PID must not be replaced by endpoint health')
    monkeypatch.setattr(serving, 'probe_local_runtime', forbidden)
    result = await serving.wait_for_managed_runtime(manager.state_path, manager.log_dir, sid, timeout=1)
    assert result['state'] == 'crashed'
    assert result['ok'] is False


@pytest.mark.asyncio
async def test_wait_refuses_external_receipt(controlled_runtime):
    from src.model_serving_status import wait_for_managed_runtime
    control, manager, children = controlled_runtime
    with pytest.raises(ValueError, match='receipt'):
        await wait_for_managed_runtime(manager.state_path, manager.log_dir, 'external-ollama', timeout=1)
    assert children == []


@pytest.mark.asyncio
async def test_wait_respects_cancel_without_stopping_process(controlled_runtime, tmp_path):
    import json
    from src.model_serving_status import wait_for_managed_runtime
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    state = json.loads(manager.state_path.read_text())
    state['tasks'][0]['status'] = 'cancel_requested'
    manager.state_path.write_text(json.dumps(state))
    result = await wait_for_managed_runtime(manager.state_path, manager.log_dir, sid, timeout=1)
    assert result['state'] == 'stopping'
    assert children[0].poll() is None


@pytest.mark.asyncio
async def test_crash_during_probe_is_not_reported_as_startup_timeout(controlled_runtime, tmp_path, monkeypatch):
    import asyncio
    from src import model_serving_status as serving
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    entered = []
    async def blocked_probe(*args, **kwargs):
        entered.append(True)
        children[0].terminate()
        children[0].wait(timeout=5)
        await asyncio.sleep(10)
    monkeypatch.setattr(serving, 'probe_local_runtime', blocked_probe)
    # Include cold HTTP-client/thread-pool setup; expiry before probe is not this race.
    result = await serving.wait_for_managed_runtime(manager.state_path, manager.log_dir, sid, timeout=2)
    assert entered, 'Test budget expired before the race-producing probe ran'
    assert result['state'] == 'crashed'
    assert result['process_ownership'] == 'gone'
