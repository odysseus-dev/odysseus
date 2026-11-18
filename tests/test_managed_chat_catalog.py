"""Typed serving to chat catalog contract; real tiny HTTP peers, no inference."""
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from tests.helpers.database import disposable_database
from tests.test_model_serving_control import controlled_runtime, plan_for


@pytest.fixture
def chat_app(controlled_runtime, tmp_path, monkeypatch):
    import core.database as database
    import routes.cookbook_routes as routes
    import routes.model_routes as models
    from src import model_serving_status as status
    control, manager, children = controlled_runtime
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, 'SessionLocal', factory)
        monkeypatch.setattr(models, '_probe_endpoint', lambda *args, **kwargs: None)
        monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', manager.state_path)
        monkeypatch.setattr(routes, 'TMUX_LOG_DIR', manager.log_dir)
        monkeypatch.setenv('AUTH_ENABLED', 'false')
        read = status.read_local_services
        monkeypatch.setattr(status, 'read_local_services', lambda *args:
            [row for row in read(*args) if row['source'] != 'discovery'])
        invalidated = []
        app = FastAPI()
        app.state.invalidate_models_cache = lambda: invalidated.append(True)
        app.include_router(routes.setup_cookbook_routes())
        yield app, factory, control, manager, children, invalidated


def launch_body(control, tmp_path):
    plan = plan_for(control, tmp_path)
    body = {key: plan[key] for key in ('engine', 'executable', 'model_path', 'port', 'context_length', 'gpu_layers')}
    body.update(model_context_limit=2048, kv_bytes_per_token=1024, display_name='Friendly display label')
    return plan, body


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12345)), base_url='http://localhost')


@pytest.mark.asyncio
async def test_typed_registration_preserves_unmanaged_same_url_configuration(chat_app, tmp_path):
    from core.database import ModelEndpoint
    app, factory, control, manager, children, invalidated = chat_app
    plan, body = launch_body(control, tmp_path)
    with factory() as db:
        db.add(ModelEndpoint(id='user-configured', name='Keep this configuration',
            base_url=f"http://localhost:{plan['port']}/v1", endpoint_kind='api', is_enabled=False))
        db.commit()
    async with client_for(app) as client:
        response = await client.post('/api/cookbook/serving/start', json=body)
        assert response.status_code == 200, response.text
        started = response.json()
        try:
            assert started['endpoint_id'] != 'user-configured', 'A typed launch must not adopt a user endpoint'
            with factory() as db:
                managed = db.get(ModelEndpoint, started['endpoint_id'])
                assert managed.base_url == plan['base_url'] + '/v1'
                assert not managed.pinned_models, 'A display label is not a runtime model ID'
                assert not managed.cached_models, 'Loading is not verified readiness'
        finally:
            await client.post('/api/cookbook/serving/stop', json={'session_id': started['session_id']})
    with factory() as db:
        original = db.get(ModelEndpoint, 'user-configured')
        assert original is not None and original.name == 'Keep this configuration'
        assert original.endpoint_kind == 'api' and original.is_enabled is False
        assert original.cookbook_serve_session_id is None
    assert invalidated, 'The existing model cache must be invalidated after catalog mutation'


@pytest.mark.asyncio
async def test_selector_links_actual_ready_model_and_follows_restart(chat_app, tmp_path):
    from core.database import ModelEndpoint
    app, factory, control, manager, children, invalidated = chat_app
    plan, body = launch_body(control, tmp_path)
    async with client_for(app) as client:
        started = (await client.post('/api/cookbook/serving/start', json=body)).json()
        sid = started['session_id']
        try:
            ready = await client.post('/api/cookbook/serving/wait-ready', json={'session_id': sid, 'timeout_seconds': 3})
            assert ready.json()['state'] == 'ready', ready.text
            services = (await client.get('/api/cookbook/serving/status')).json()['services']
            service = next(row for row in services if row['id'] == sid)
            assert service['endpoint_id'] == started['endpoint_id']
            assert service['models'] == ['fixture-only-not-a-model']
            assert body['display_name'] not in service['models']
            restarted = await client.post('/api/cookbook/serving/restart', json={'session_id': sid})
            assert restarted.status_code == 200, restarted.text
            replacement = restarted.json()
            old_sid, sid = sid, replacement['session_id']
            assert replacement['endpoint_id'] != started['endpoint_id']
            with factory() as db:
                assert db.get(ModelEndpoint, started['endpoint_id']) is None
                endpoint = db.get(ModelEndpoint, replacement['endpoint_id'])
                assert endpoint.cookbook_serve_session_id == sid
                assert endpoint.name == body['display_name']
            reused = (await client.post('/api/cookbook/serving/start', json=body)).json()
            assert reused['reused'] and reused['endpoint_id'] == replacement['endpoint_id']
        finally:
            await client.post('/api/cookbook/serving/stop', json={'session_id': sid})


@pytest.mark.asyncio
async def test_managed_ollama_catalog_uses_requested_port_not_default(chat_app, tmp_path):
    from core.database import ModelEndpoint
    from tests.test_model_serving_control import free_port
    import os
    app, factory, control, manager, children, invalidated = chat_app
    exe = tmp_path / ('ollama.exe' if os.name == 'nt' else 'ollama')
    exe.write_bytes(b'fixture placeholder; never executed')
    port = free_port()
    body = {'engine': 'ollama', 'executable': str(exe), 'port': port, 'display_name': 'Local daemon'}
    async with client_for(app) as client:
        response = await client.post('/api/cookbook/serving/start', json=body)
        assert response.status_code == 200, response.text
        data = response.json()
        try:
            with factory() as db:
                endpoint = db.get(ModelEndpoint, data['endpoint_id'])
                assert endpoint.base_url == f'http://127.0.0.1:{port}/v1'
                assert endpoint.endpoint_kind == 'ollama'
                assert not endpoint.pinned_models and not endpoint.cached_models
        finally:
            await client.post('/api/cookbook/serving/stop', json={'session_id': data['session_id']})


def test_legacy_verified_stop_reconciles_typed_worker_after_observer_loss(chat_app, tmp_path, monkeypatch):
    import routes.cookbook_routes as routes
    from src.native_workers import registry_for
    app, factory, control, manager, children, invalidated = chat_app
    registry = registry_for(str(manager.state_path.parent / 'process_resources'))
    monkeypatch.setattr(registry, 'finish', lambda *args: None)
    monkeypatch.setattr(manager, '_observe_exit', lambda *args: None)
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    receipt = manager._receipt(sid)
    routes._set_task_status(sid, 'cancel_requested', stamp=True)
    assert registry.snapshot()['workers'][0]['state'] == 'running'
    routes._set_task_status(sid, 'stopped', stamp=True)
    assert registry.snapshot()['workers'][0]['state'] == 'running', 'A badge cannot confirm process death'
    assert children[0].poll() is None
    outcome = control.kill_process_tree(receipt['pid'], start_token=receipt['start_token'],
        pgid=receipt['pgid'], require_identity=True)
    assert outcome.dead
    routes._set_task_status(sid, 'stopped', stamp=True)
    row = registry.snapshot()['workers'][0]
    assert row['state'] == 'cancelled'
    assert row['exit_code'] is None and row['completion_basis'] == 'verified-stop'
