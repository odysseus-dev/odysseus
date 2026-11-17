"""Typed routes preserve native-control gates; real child is a tiny fixture."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as routes
from tests.test_model_serving_control import controlled_runtime, plan_for
from tests.test_model_serving_status import runtime_server


@pytest.mark.asyncio
async def test_api_start_readiness_reconnect_and_stop(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    from src import model_serving_status as serving
    read_services = serving.read_local_services
    monkeypatch.setattr(serving, 'read_local_services', lambda *args: [
        service for service in read_services(*args) if service['source'] != 'discovery'])
    plan = plan_for(control, tmp_path)
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', manager.state_path)
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', manager.log_dir)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    body = {key: plan[key] for key in ('engine', 'executable', 'model_path', 'port', 'context_length', 'gpu_layers')}
    body.update(model_context_limit=2048, kv_bytes_per_token=1024)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12345)), base_url='http://localhost') as client:
        planned = await client.post('/api/cookbook/serving/plan', json=body)
        assert planned.status_code == 200, planned.text
        assert not children, 'planning must not spawn anything'
        started = await client.post('/api/cookbook/serving/start', json=body)
        assert started.status_code == 200, started.text
        sid = started.json()['session_id']
        waited = await client.post('/api/cookbook/serving/wait-ready', json={'session_id': sid, 'timeout_seconds': 3})
        assert waited.status_code == 200, waited.text
        observed = waited.json()
        assert observed['readiness'] == 'ready'
        assert observed['models'] == ['fixture-only-not-a-model']
        # Simulate app/controller reconstruction; no Popen handle is needed.
        again = FastAPI()
        again.include_router(routes.setup_cookbook_routes())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=again, client=('127.0.0.1', 12345)), base_url='http://localhost') as fresh:
            status = await fresh.get('/api/cookbook/serving/status')
            service = next(s for s in status.json()['services'] if s['id'] == sid)
            assert service['controls']['stop'] is True
            assert service['management'] == 'odysseus-managed'
            restarted = await fresh.post('/api/cookbook/serving/restart', json={'session_id': sid})
            assert restarted.status_code == 200, restarted.text
            assert restarted.json()['previous_session_id'] == sid
            sid = restarted.json()['session_id']
            assert len(children) == 2
            for _ in range(30):
                status = await fresh.get('/api/cookbook/serving/status')
                service = next(s for s in status.json()['services'] if s['id'] == sid)
                if service['readiness'] == 'ready':
                    break
                await asyncio.sleep(0.05)
            assert service['readiness'] == 'ready'
            assert service['controls']['restart'] is True
            stopped = await fresh.post('/api/cookbook/serving/stop', json={'session_id': sid})
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()['process_stopped'] is True
        saved = json.loads(manager.state_path.read_text(encoding='utf-8'))
        assert saved['tasks'][0]['status'] == 'stopped'


@pytest.mark.asyncio
async def test_restart_api_reports_old_process_stopped_when_replacement_fails(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    plan = plan_for(control, tmp_path)
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', manager.state_path)
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', manager.log_dir)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    body = {key: plan[key] for key in ('engine', 'executable', 'model_path', 'port', 'context_length', 'gpu_layers')}
    body.update(model_context_limit=2048, kv_bytes_per_token=1024)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12345)), base_url='http://localhost') as client:
        started = await client.post('/api/cookbook/serving/start', json=body)
        assert started.status_code == 200, started.text
        def fail_spawn(*args, **kwargs):
            raise OSError('fixture replacement spawn failed')
        monkeypatch.setattr(control, '_spawn_runtime', fail_spawn)
        restarted = await client.post('/api/cookbook/serving/restart', json={'session_id': started.json()['session_id']})
        assert restarted.status_code == 409
        detail = restarted.json()['detail']
        assert detail['state'] == 'restart-failed'
        assert detail['process_stopped'] is True
        assert detail['error'] == 'fixture replacement spawn failed'
    assert len(children) == 1
    assert children[0].poll() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['plan', 'start', 'stop', 'restart', 'wait-ready'])
@pytest.mark.parametrize('gate', ['internal-tool', 'cross-site'])
async def test_mutations_preserve_human_admin_boundary(tmp_path, monkeypatch, operation, gate):
    from core.middleware import INTERNAL_TOOL_HEADER
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    body = {'session_id': 'serve-deadbeef'} if operation in {'stop', 'restart', 'wait-ready'} else {
        'engine': 'llamacpp', 'executable': 'not-executed', 'model_path': 'not-read', 'port': 18081}
    headers = {INTERNAL_TOOL_HEADER: 'fixture-not-a-credential'} if gate == 'internal-tool' else {'sec-fetch-site': 'cross-site'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12345)), base_url='http://localhost') as client:
        response = await client.post('/api/cookbook/serving/' + operation, json=body, headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_healthy_external_ollama_never_gains_stop_permission(runtime_server, tmp_path, monkeypatch):
    from src import model_serving_status as serving
    from src import model_serving_control as control
    url, responses, _ = runtime_server
    responses.update({'/api/version': (200, {'version': 'external-fixture'}),
                      '/api/tags': (200, {'models': []}), '/api/ps': (200, {'models': []})})
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', tmp_path / 'state.json')
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', tmp_path)
    monkeypatch.setattr(serving, 'read_local_services', lambda *args: [{
        'id': 'external-ollama', 'source': 'discovery', 'engine': 'ollama', 'base_url': url,
        'pid': None, 'process_ownership': 'external', 'task_status': None, 'management': 'external'}])
    def forbidden_signal(*args, **kwargs):
        raise AssertionError('An HTTP endpoint is not signal authority')
    monkeypatch.setattr(control, 'kill_process_tree', forbidden_signal)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12345)), base_url='http://localhost') as client:
        status = (await client.get('/api/cookbook/serving/status')).json()['services'][0]
        assert status['readiness'] == 'ready'
        assert status['controls']['stop'] is False
        refused = await client.post('/api/cookbook/serving/stop', json={'session_id': 'external-ollama'})
        assert refused.status_code == 409
        refused_restart = await client.post('/api/cookbook/serving/restart', json={'session_id': 'external-ollama'})
        assert refused_restart.status_code == 409
    async with httpx.AsyncClient(trust_env=False) as client:
        assert (await client.get(url + '/api/version')).status_code == 200
