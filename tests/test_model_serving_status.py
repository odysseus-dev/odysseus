"""Serving status: real loopback HTTP fixtures; never load a model."""
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from src import model_serving_status as serving
from src.process_ownership import start_token


def task(cmd="llama-server --port 8081", **extra):
    return {"sessionId": "serve-lab", "type": "serve", "status": "running", "payload": {"_cmd": cmd}, **extra}


@pytest.mark.parametrize("cmd,engine,url", [
    ('"C:/runtime/llama-server.exe" --port 8081', 'llamacpp', 'http://127.0.0.1:8081'),
    ('llama-server --port=8082', 'llamacpp', 'http://127.0.0.1:8082'),
    ('OLLAMA_HOST=127.0.0.1:11435 ollama serve', 'ollama', 'http://127.0.0.1:11435'),
    ('ollama serve', 'ollama', 'http://127.0.0.1:11434'),
])
def test_local_target(cmd, engine, url):
    assert serving.local_serving_target(task(cmd)) == {"engine": engine, "base_url": url}


@pytest.mark.parametrize('entry', [
    task(remoteHost='server'), task('python -m pip install llama-cpp-python'),
    task('python -m llama_cpp.server --port 8080'),
    task('OLLAMA_HOST=https://example.com ollama serve'),
    task('OLLAMA_HOST=127.0.0.1:99999 ollama serve'), task('llama-server --port 99999'),
    task('docker exec ollama ollama serve'),
])
def test_nonlocal_or_nonruntime_is_not_probed(entry):
    assert serving.local_serving_target(entry) is None


def test_restart_recovers_receipt_but_missing_token_is_not_owned(tmp_path):
    state = tmp_path / 'cookbook_state.json'
    state.write_text(json.dumps({'tasks': [task()]}), encoding='utf-8')
    (tmp_path / 'serve-lab.pid').write_text(str(os.getpid()), encoding='utf-8')
    services = serving.read_local_services(state, tmp_path)
    assert services[0]['process_ownership'] == 'unverifiable'
    (tmp_path / 'serve-lab.pid.token').write_text(start_token(os.getpid()), encoding='utf-8')
    recovered = serving.read_local_services(state, tmp_path)
    assert recovered[0]['process_ownership'] == 'owned'
    assert recovered[1]['process_ownership'] == 'external'


@pytest.fixture
def runtime_server():
    responses = {}
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(('GET', self.path))
            status, data = responses.get(self.path, (404, {'error': 'fixture endpoint absent'}))
            encoded = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', responses, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_llama_loading_then_ready_over_real_http(runtime_server):
    url, responses, calls = runtime_server
    responses['/health'] = (503, {'error': {'message': 'Loading model'}})
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await serving.probe_local_runtime(client, 'llamacpp', url)
        assert result['readiness'] == 'loading'
        responses.update({
            '/health': (200, {'status': 'ok'}),
            '/models': (200, {'data': [{'id': 'fixture-only'}]}),
            '/props': (200, {'default_generation_settings': {'n_ctx': 4096}}),
        })
        result = await serving.probe_local_runtime(client, 'llamacpp', url)
        assert result['readiness'] == 'ready'
        assert result['models'] == ['fixture-only']
        assert result['context_length'] == 4096
    assert all(method == 'GET' for method, _ in calls)


@pytest.mark.asyncio
async def test_external_ollama_metadata_does_not_load_a_model(runtime_server):
    url, responses, calls = runtime_server
    responses.update({
        '/api/version': (200, {'version': 'fixture'}),
        '/api/tags': (200, {'models': [{'name': 'fixture-only'}]}),
        '/api/ps': (200, {'models': [{'name': 'fixture-only', 'context_length': 8192, 'size_vram': 0}]}),
    })
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await serving.probe_local_runtime(client, 'ollama', url)
    assert result['models'] == ['fixture-only']
    assert result['loaded_models'][0]['context_length'] == 8192
    assert {p for _, p in calls} == {'/api/version', '/api/tags', '/api/ps'}


@pytest.mark.asyncio
async def test_nonloopback_probe_refused_before_network():
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(ValueError):
            await serving.probe_local_runtime(client, 'ollama', 'http://example.com:11434')


@pytest.mark.asyncio
async def test_bad_runtime_shapes_are_not_reported_ready(runtime_server):
    url, responses, _ = runtime_server
    responses.update({'/health': (200, {}), '/models': (200, {'data': None})})
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await serving.probe_local_runtime(client, 'llamacpp', url)
    assert result['readiness'] == 'error'
    assert result['models'] == []


@pytest.mark.parametrize('ownership,status,readiness,expected', [
    ('owned', 'running', 'ready', 'ready'),
    ('owned', 'running', 'loading', 'loading'),
    ('owned', 'running', 'unavailable', 'unresponsive'),
    ('gone', 'running', 'ready', 'crashed'),
    ('gone', 'stopped', 'ready', 'stopped'),
    ('foreign', 'running', 'ready', 'ownership-lost'),
    ('unverifiable', 'running', 'ready', 'unverifiable'),
    ('external', None, 'ready', 'external'),
])
def test_lifecycle_never_confuses_health_with_ownership(ownership, status, readiness, expected):
    assert serving.lifecycle_state(ownership, status, readiness) == expected


@pytest.mark.asyncio
async def test_status_api_is_read_only_and_recovers_existing_process(monkeypatch, tmp_path, runtime_server):
    from fastapi import FastAPI
    import routes.cookbook_routes as routes

    url, responses, _ = runtime_server
    responses.update({'/health': (200, {'status': 'ok'}), '/models': (200, {'data': []})})
    entry = {'id': 'serve-lab', 'source': 'cookbook', 'engine': 'llamacpp',
             'base_url': url, 'pid': os.getpid(), 'process_ownership': 'owned', 'task_status': 'running'}
    monkeypatch.setattr(serving, 'read_local_services', lambda *args: [entry])
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', tmp_path / 'state.json')
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/cookbook/serving/status')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['schema_version'] == 1
    service = data['services'][0]
    assert service['lifecycle_state'] == 'ready'
    assert service['process_ownership'] == 'owned'
    assert service['endpoint_ownership_verified'] is False
    assert service['controls'] == {'start': False, 'stop': False, 'restart': False}
    assert not (tmp_path / 'state.json').exists()


@pytest.mark.asyncio
async def test_sleeping_llama_observation_never_wakes_it(runtime_server):
    url, responses, calls = runtime_server
    responses.update({
        '/health': (200, {'status': 'ok'}),
        '/props': (200, {'is_sleeping': True, 'modalities': {'vision': False}}),
    })
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await serving.probe_local_runtime(client, 'llamacpp', url)
    assert result['readiness'] == 'sleeping'
    assert result['capabilities']['modalities'] == {'vision': False}
    assert {p for _, p in calls} == {'/health', '/props', '/models'}


def test_exited_receipt_is_detected_after_restart(tmp_path):
    import subprocess
    import sys

    # This tiny child is not a model runtime. It only exercises persisted PID
    # identity across a real process exit, without signals or GPU activity.
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.3)'])
    try:
        token = start_token(child.pid)
        assert token
        (tmp_path / 'serve-lab.pid').write_text(str(child.pid), encoding='utf-8')
        (tmp_path / 'serve-lab.pid.token').write_text(token, encoding='utf-8')
        child.wait(timeout=5)
        identity = serving.task_process_identity(task(), tmp_path)
        assert identity['process_ownership'] == 'gone'
        assert serving.lifecycle_state(identity['process_ownership'], 'running', 'ready') == 'crashed'
    finally:
        child.wait(timeout=5)


@pytest.mark.asyncio
async def test_serving_status_requires_admin(monkeypatch):
    from fastapi import FastAPI, HTTPException
    import routes.cookbook_routes as routes

    def deny(request):
        raise HTTPException(403, 'Admin only')
    monkeypatch.setattr(routes, 'require_admin', deny)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/cookbook/serving/status')
    assert response.status_code == 403


def test_python_server_is_distinct_and_never_native_managed(tmp_path):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'tasks': [task('python -m llama_cpp.server --port 8080')]}), encoding='utf-8')
    services = serving.read_local_services(state, tmp_path)
    python = next(service for service in services if service['id'] == 'serve-lab')
    assert python['engine'] == 'llama_cpp_python'
    assert python['base_url'] is None, 'native health probes must not wake a Python model server'
    assert python['management'] == 'unmanaged-python'
