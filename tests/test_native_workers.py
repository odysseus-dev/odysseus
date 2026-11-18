"""Durable producer observations; only tiny owned subprocesses, never models."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest


def registry_at(tmp_path):
    assert importlib.util.find_spec('src.native_workers'), 'durable worker tracking is missing'
    from src.native_workers import WorkerRegistry
    return WorkerRegistry(tmp_path / 'application-data' / 'native_workers.json')


def test_intent_is_durable_before_spawn_and_fence_rejects_new_launch(tmp_path):
    registry = registry_at(tmp_path)
    def spawn():
        data = json.loads(registry.path.read_text())
        assert next(iter(data['workers'].values()))['state'] == 'intended'
        return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.15)'])
    proc = registry.spawn('shell', 'fixture-session', spawn)
    proc.wait(timeout=5)
    deadline = time.monotonic() + 3
    while registry.snapshot()['workers'][0]['state'] != 'completed' and time.monotonic() < deadline:
        time.sleep(.01)
    assert registry.snapshot()['workers'][0]['state'] == 'completed'
    token = registry.fence()
    from src.native_workers import AdmissionClosed
    with pytest.raises(AdmissionClosed):
        registry.spawn('shell', 'must-not-start', lambda: pytest.fail('Unadmitted spawn reached'))
    registry.resume(token)


@pytest.mark.asyncio
async def test_async_worker_is_recovered_without_temp_logs_and_cancel_waits_for_exit(tmp_path):
    import asyncio
    import shutil
    registry = registry_at(tmp_path)
    temporary = tmp_path / 'windows-temp'
    temporary.mkdir()
    (temporary / 'legacy.log').write_text('fixture only')
    proc = await registry.spawn_async('shell', 'async-session', lambda: asyncio.create_subprocess_exec(
        sys.executable, '-c', 'import time; time.sleep(.4)'))
    generation = proc._odysseus_worker_generation
    registry.request_cancel(generation)
    shutil.rmtree(temporary)
    recovered = type(registry)(registry.path)
    assert recovered.snapshot()['workers'][0]['state'] == 'running'
    await proc.wait()
    deadline = time.monotonic() + 3
    while recovered.snapshot()['workers'][0]['state'] != 'cancelled' and time.monotonic() < deadline:
        await asyncio.sleep(.01)
    assert recovered.snapshot()['workers'][0]['state'] == 'cancelled'
    assert recovered.snapshot()['signal_authorized'] is False


def test_lost_initialized_inventory_blocks_without_manufacturing_replacement(tmp_path):
    registry = registry_at(tmp_path)
    registry.initialize()
    registry.path.unlink()
    recovered = type(registry)(registry.path)
    assert recovered.snapshot()['unknown'] is True
    from src.native_workers import WorkerStateUnknown
    with pytest.raises(WorkerStateUnknown):
        recovered.spawn('shell', 'never', lambda: pytest.fail('Lost registry permitted a launch'))
    assert not recovered.path.exists()


def test_malformed_worker_record_is_unknown_not_an_exception_or_idle(tmp_path):
    registry = registry_at(tmp_path)
    registry.initialize()
    data = json.loads(registry.path.read_text())
    data['workers']['invalid'] = {}
    registry.path.write_text(json.dumps(data))
    before = registry.path.read_bytes()
    assert registry.snapshot()['unknown'] is True
    assert registry.path.read_bytes() == before


def test_fence_is_enforced_by_a_separate_python_producer(tmp_path):
    registry = registry_at(tmp_path)
    registry.initialize()
    token = registry.fence()
    peer = tmp_path / 'producer.py'
    peer.write_text(
        'import sys\nfrom pathlib import Path\n'
        f'sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n'
        'from src.native_workers import WorkerRegistry, AdmissionClosed\n'
        'r = WorkerRegistry(sys.argv[1])\n'
        'try:\n r.spawn("shell", "other-producer", lambda: sys.exit(9))\n'
        'except AdmissionClosed:\n sys.exit(0)\n'
        'sys.exit(8)\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(peer), str(registry.path)], timeout=8)
    assert result.returncode == 0
    assert registry.snapshot()['workers'] == []
    registry.resume(token)


@pytest.mark.asyncio
async def test_windows_detached_shell_publishes_worker_before_disconnect(tmp_path, monkeypatch):
    import asyncio
    import os
    from types import SimpleNamespace
    if os.name != 'nt':
        pytest.skip('Native Windows shell producer')
    import routes.shell_routes as routes
    from src import native_workers
    registry = registry_at(tmp_path)
    monkeypatch.setattr(native_workers, 'default_registry', lambda: registry)
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', tmp_path / 'durable-sessions')
    children = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        proc = original(*args, **kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr(subprocess, 'Popen', capture)
    async def disconnected():
        return True
    try:
        command = f'"{sys.executable}" -c "import time; time.sleep(.2)"'
        async for _ in routes._generate_win_detached(command, SimpleNamespace(is_disconnected=disconnected)):
            pass
        state = registry.snapshot()
        assert state['initialized'] is True
        assert len(state['workers']) == 1
        assert state['workers'][0]['source'] == 'shell'
    finally:
        for proc in children:
            await asyncio.to_thread(proc.wait, timeout=5)


@pytest.mark.asyncio
async def test_admitted_async_spawn_remains_visible_while_fence_closes_admission(tmp_path):
    import asyncio
    registry = registry_at(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def factory():
        entered.set()
        await release.wait()
        return await asyncio.create_subprocess_exec(sys.executable, '-c', 'pass')
    task = asyncio.create_task(registry.spawn_async('shell', 'admitted', factory))
    await entered.wait()
    token = registry.fence()
    try:
        assert registry.snapshot()['workers'][0]['state'] == 'intended'
    finally:
        release.set()
        proc = await task
        await proc.wait()
        registry.resume(token)


@pytest.mark.asyncio
async def test_shutdown_fences_producers_before_any_guard_and_resumes_on_refusal(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src import windows_app_restart as control
    from src.native_workers import registry_for
    state = tmp_path / 'data'
    registry = registry_for(str(state / 'process_resources'))
    registry.initialize()
    def guard(*args, **kwargs):
        assert registry.snapshot()['draining'] is True
        return {'clear': False, 'errors': ['fixture busy']}
    monkeypatch.setattr(control, 'full_guard', guard)
    server = SimpleNamespace(should_exit=False)
    gate = control.Admission(None)
    result = await control.prepare_shutdown(server, gate, {'state_dir': str(state)}, {}, [], timeout=1)
    assert result['ok'] is False
    assert registry.snapshot()['draining'] is False
    assert gate.paused is False


@pytest.mark.asyncio
async def test_readonly_worker_api_exposes_real_counters_without_creating_inventory(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import routes.cookbook_routes as routes
    from src.native_mcp_activity import REGISTRY
    state = tmp_path / 'data' / 'cookbook_state.json'
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', state)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/cookbook/workers/status')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['schema_version'] == 1
    assert data['inventory']['initialized'] is False
    assert data['mcp']['initialized'] == REGISTRY.snapshot()['initialized']
    assert not state.parent.exists(), 'Diagnostics must never initialize missing evidence'


@pytest.mark.asyncio
async def test_task_with_lost_log_reports_unavailable_without_fake_output(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import routes.cookbook_routes as routes
    state = tmp_path / 'cookbook_state.json'
    state.write_text(json.dumps({'tasks': [{
        'sessionId': 'cookbook-lostlog', 'type': 'download', 'platform': 'windows',
        'status': 'done', 'output': '', 'payload': {'repo_id': 'fixture/model',
        'include': '*Q4_K_M*', 'local_dir': str(tmp_path)}}], 'env': {'servers': []}}))
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', state)
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', tmp_path / 'missing-logs')
    monkeypatch.setattr(routes, '_local_probe_python', lambda: sys.executable)
    monkeypatch.setattr(routes, 'IS_WINDOWS', True)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/cookbook/tasks/status')
    task = response.json()['tasks'][0]
    assert task['status'] == 'error'
    assert task['log_availability'] == 'unavailable'
    assert 'unavailable' in task['log_unavailable_reason']
    assert task['output_tail'] == ''


@pytest.mark.parametrize('verdict,recover', [('owned', False), ('gone', True), ('foreign', True), ('unverifiable', False)])
def test_new_epoch_never_clears_live_or_unknown_fence_owner(tmp_path, monkeypatch, verdict, recover):
    from src import native_workers
    registry = registry_at(tmp_path)
    registry.initialize()
    old = {'pid': 424242, 'start_token': 'win32:1'}
    data = json.loads(registry.path.read_text())
    data['fence'] = {'token': 'fixture-fence', 'manager': old}
    registry.path.write_text(json.dumps(data))
    original = native_workers.ownership.verify
    monkeypatch.setattr(native_workers.ownership, 'verify',
        lambda pid, token: verdict if pid == old['pid'] else original(pid, token))
    alive = native_workers.pid_alive
    monkeypatch.setattr(native_workers, 'pid_alive',
        lambda pid: True if pid == old['pid'] else alive(pid))
    if recover:
        registry.initialize()
        assert registry.snapshot()['draining'] is False
    else:
        with pytest.raises((native_workers.AdmissionClosed, native_workers.WorkerStateUnknown)):
            registry.initialize()
        assert registry.snapshot()['draining'] is True


def test_foreign_worker_identity_is_unknown_without_signal_authority(tmp_path, monkeypatch):
    from src import native_workers
    registry = registry_at(tmp_path)
    proc = registry.spawn('shell', 'foreign-slot', lambda: subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(.3)']))
    original = native_workers.ownership.verify
    monkeypatch.setattr(native_workers.ownership, 'verify',
        lambda pid, token: 'foreign' if pid == proc.pid else original(pid, token))
    try:
        snapshot = registry.snapshot()
        assert snapshot['workers'][0]['state'] == 'unknown'
        assert snapshot['unknown'] and not snapshot['signal_authorized']
        assert proc.poll() is None
    finally:
        proc.wait(timeout=5)


@pytest.mark.parametrize('background_published', [False, True])
def test_native_index_does_not_hide_missing_background_history(tmp_path, monkeypatch, background_published):
    from tests.test_windows_app_restart import files
    from src import windows_app_restart as control
    from src.native_workers import registry_for
    state, logs, db = files(tmp_path)
    (state / 'bg_jobs.json').unlink()
    registry = registry_for(str(state / 'process_resources'))
    registry.initialize()
    if background_published:
        proc = registry.spawn('background', 'fixture', lambda: subprocess.Popen([sys.executable, '-c', 'pass']))
        proc.wait(timeout=5)
    monkeypatch.setattr(control, '_native_background_processes', lambda: {'complete': True, 'supervisors': []})
    result = control.file_guard(state, logs, db)
    assert result['clear'] is (not background_published)
    assert not (state / 'bg_jobs.json').exists()


def test_native_python_launch_preserves_venv_and_actual_child_identity():
    from core.platform_compat import native_python_command
    command, env = native_python_command([sys.executable, '-c',
        'import os,sys,json; print(json.dumps(dict(pid=os.getpid(),prefix=sys.prefix)))'])
    proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
    result = json.loads(proc.communicate(timeout=5)[0])
    assert proc.returncode == 0
    assert proc.pid == result['pid']
    assert result['prefix'] == sys.prefix


@pytest.mark.parametrize('source,sid', [('cookbook', 'cookbook-linked'), ('serving', 'serve-deadbeef')])
def test_cookbook_cancel_links_to_existing_worker_without_signalling(tmp_path, monkeypatch, source, sid):
    import routes.cookbook_routes as routes
    from src.native_workers import registry_for
    state = tmp_path / 'cookbook_state.json'
    state.write_text(json.dumps({'tasks': [{'sessionId': sid, 'status': 'running'}]}))
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', state)
    registry = registry_for(str(tmp_path / 'process_resources'))
    proc = registry.spawn(source, sid, lambda: subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(.4)']))
    try:
        routes._set_task_status(sid, 'cancel_requested', stamp=True)
        assert registry.snapshot()['workers'][0]['cancel_requested'] is True
        assert proc.poll() is None, 'Recording cancellation is not signal authority'
    finally:
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_abandon_an_admitted_spawn(tmp_path):
    import asyncio
    registry = registry_at(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def factory():
        entered.set()
        await release.wait()
        return await asyncio.create_subprocess_exec(sys.executable, '-c', 'pass')
    caller = asyncio.create_task(registry.spawn_async('shell', 'cancelled-caller', factory))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert registry.snapshot()['workers'][0]['state'] == 'intended'
    release.set()
    deadline = time.monotonic() + 5
    while registry.snapshot()['workers'][0]['state'] != 'completed' and time.monotonic() < deadline:
        await asyncio.sleep(.01)
    assert registry.snapshot()['workers'][0]['state'] == 'completed'


@pytest.mark.asyncio
async def test_worker_status_keeps_admin_gate(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI, HTTPException
    import routes.cookbook_routes as routes
    def deny(request):
        raise HTTPException(403, 'fixture admin required')
    monkeypatch.setattr(routes, 'require_admin', deny)
    monkeypatch.setattr(routes, '_COOKBOOK_STATE_PATH', tmp_path / 'untouched' / 'state.json')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/cookbook/workers/status')).status_code == 403
    assert not (tmp_path / 'untouched').exists()
