"""Serving joins producer admission; fixtures never load a model or touch a GPU."""
import time

import pytest

from src.native_workers import AdmissionClosed, registry_for
from tests.test_model_serving_control import controlled_runtime, plan_for


def test_serving_publishes_before_spawn_and_cannot_bypass_fence(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    registry = registry_for(str(manager.state_path.parent / 'process_resources'))
    spawn = control._spawn_runtime

    def checked_spawn(plan, log):
        rows = registry.snapshot()['workers']
        assert len(rows) == 1, 'Serving must publish its intent before creating the child'
        assert rows[0]['source'] == 'serving'
        assert rows[0]['state'] == 'intended'
        return spawn(plan, log)

    monkeypatch.setattr(control, '_spawn_runtime', checked_spawn)
    plan = plan_for(control, tmp_path)
    started = manager.start(plan)
    sid = started['session_id']
    row = registry.snapshot()['workers'][0]
    assert row['source_id'] == sid
    assert row['identity']['pid'] == children[0].pid
    assert row['identity']['start_token'] == manager._receipt(sid)['start_token']
    assert manager.stop(sid)['process_stopped'] is True
    deadline = time.monotonic() + 3
    while type(registry.snapshot()['workers'][0]['exit_code']) is not int and time.monotonic() < deadline:
        time.sleep(.01)
    row = registry.snapshot()['workers'][0]
    assert row['state'] == 'cancelled'
    assert type(row['exit_code']) is int
    assert registry.snapshot()['signal_authorized'] is False
    token = registry.fence()
    try:
        with pytest.raises(AdmissionClosed):
            manager.start(plan)
        assert len(children) == 1
    finally:
        registry.resume(token)


def test_reconstructed_stop_without_exit_observer_does_not_invent_exit_code(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    registry = registry_for(str(manager.state_path.parent / 'process_resources'))
    # Simulate lost observers, not a fake OS stop or a fabricated return code.
    monkeypatch.setattr(registry, 'finish', lambda *args: None)
    monkeypatch.setattr(manager, '_observe_exit', lambda *args: None)
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    recovered = control.LocalServingController(manager.state_path, manager.log_dir)
    assert not recovered._children
    assert recovered.stop(sid)['process_stopped'] is True
    assert children[0].poll() is not None
    row = registry.snapshot()['workers'][0]
    assert row['state'] == 'cancelled'
    assert row['exit_code'] is None
    assert row['completion_basis'] == 'verified-stop'
    assert registry.snapshot()['signal_authorized'] is False


def test_confirmed_stop_refuses_live_unknown_or_wrong_identity(controlled_runtime, tmp_path, monkeypatch):
    from src import native_workers
    control, manager, children = controlled_runtime
    registry = registry_for(str(manager.state_path.parent / 'process_resources'))
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    identity = registry.snapshot()['workers'][0]['identity']
    registry.request_cancel_source('serving', sid)
    assert not registry.confirm_stopped('serving', sid, identity)
    assert not registry.confirm_stopped('serving', sid, {**identity, 'start_token': 'wrong-incarnation'})
    original = native_workers.ownership.verify
    monkeypatch.setattr(native_workers.ownership, 'verify', lambda pid, token:
        native_workers.ownership.UNVERIFIABLE if pid == identity['pid'] else original(pid, token))
    assert not registry.confirm_stopped('serving', sid, identity)
    assert children[0].poll() is None, 'Observation is never permission to signal'


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', ['fenced', 'lost-index'])
async def test_serving_api_reports_admission_conflict_without_spawning(controlled_runtime, tmp_path, monkeypatch, blocked):
    import httpx
    from fastapi import FastAPI
    import routes.cookbook_routes as routes
    control, manager, children = controlled_runtime
    registry = registry_for(str(manager.state_path.parent / 'process_resources'))
    registry.initialize()
    token = registry.fence() if blocked == 'fenced' else None
    if blocked == 'lost-index':
        registry.path.unlink()
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', manager.state_path)
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', manager.log_dir)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    plan = plan_for(control, tmp_path)
    body = {key: plan[key] for key in ('engine', 'executable', 'model_path', 'port', 'context_length', 'gpu_layers')}
    body.update(model_context_limit=2048, kv_bytes_per_token=1024)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12345)), base_url='http://localhost') as client:
            response = await client.post('/api/cookbook/serving/start', json=body)
        assert response.status_code == 409, response.text
        assert not children
        assert not manager.state_path.exists()
    finally:
        if token is not None:
            registry.resume(token)


def test_restart_reports_confirmed_stop_when_replacement_is_fenced(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    registry = registry_for(str(manager.state_path.parent / 'process_resources'))
    token = registry.fence()
    try:
        result = manager.restart(sid)
        assert result['ok'] is False
        assert result['state'] == 'restart-failed'
        assert result['process_stopped'] is True
        assert 'fenced' in result['error']
        assert len(children) == 1 and children[0].poll() is not None
    finally:
        registry.resume(token)
