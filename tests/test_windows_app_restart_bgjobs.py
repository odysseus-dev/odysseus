"""Guarded bg_jobs inventory recovery: startup init, mature-install idle proof, fail-closed negatives."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Native Windows control infrastructure')

ROOT = Path(__file__).resolve().parents[1]


def files(tmp_path, *, store=True):
    state = tmp_path / 'data'
    state.mkdir(exist_ok=True)
    (state / 'cookbook_state.json').write_text('{"tasks": []}', encoding='utf-8')
    if store:
        (state / 'bg_jobs.json').write_text('{}', encoding='utf-8')
    workers = tmp_path / 'workers'
    workers.mkdir(exist_ok=True)
    db = tmp_path / 'gateway.db'
    with closing(sqlite3.connect(db)) as con:
        con.execute('create table session_turn_leases(expires_at real)')
    return state, workers, db


def initialize_registry(state):
    from src.native_workers import registry_for
    registry = registry_for(str(state / 'process_resources'))
    registry.initialize()
    return registry


def add_terminal_worker(registry):
    proc = registry.spawn('cookbook', 'fixture-done', lambda: subprocess.Popen([sys.executable, '-c', 'pass']))
    proc.wait(timeout=10)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = registry.snapshot()['workers']
        if rows and all(row['state'] in {'completed', 'failed', 'cancelled'} for row in rows):
            return
        time.sleep(.02)
    raise AssertionError('fixture worker never reached a terminal state')


def clean_scan(monkeypatch):
    from src import windows_app_restart as control
    monkeypatch.setattr(control, '_native_background_processes', lambda: {'complete': True, 'supervisors': []})


def idle_state(tmp_path, monkeypatch):
    """Mature install: store absent, initialized all-terminal registry, clean scan."""
    state, workers, db = files(tmp_path, store=False)
    add_terminal_worker(initialize_registry(state))
    clean_scan(monkeypatch)
    return state, workers, db


def expired_grant():
    """A background grant whose supervisor really ran and really exited."""
    from src import windows_restart_os as win
    proc = subprocess.Popen([sys.executable, '-c', 'pass'], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    token = win.identity(proc.pid)['start_token']
    proc.wait(timeout=10)
    return {'id': 'g-expired', 'lifetime': 'background', 'supervisor_pid': proc.pid,
            'supervisor_token': token, 'released_at': None}


def live_grant():
    """A background grant naming THIS live process as its supervisor."""
    from src import windows_restart_os as win
    return {'id': 'g-live', 'lifetime': 'background', 'supervisor_pid': os.getpid(),
            'supervisor_token': win.identity(os.getpid())['start_token'], 'released_at': None}


def write_grants(state, records):
    (state / 'containment_grants.json').write_text(json.dumps(records), encoding='utf-8')


def remnant_job(state, job_id='abcdef012345', exit_code='0'):
    jobs = state / 'bg_jobs'
    jobs.mkdir(exist_ok=True)
    (jobs / (job_id + '.log')).write_text('output', encoding='utf-8')
    (jobs / (job_id + '.result.json')).write_text('{"exit_code": 0}', encoding='utf-8')
    if exit_code is not None:
        (jobs / (job_id + '.exit')).write_text(exit_code, encoding='utf-8')


def test_absent_store_on_mature_install_is_recovered_idle(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    released = {**expired_grant(), 'id': 'g-released', 'released_at': time.time()}
    write_grants(state, {'g1': expired_grant(), 'g2': released})
    remnant_job(state)
    (state / 'bg_jobs.json.lock').write_text('0', encoding='utf-8')  # stale lock is not a held lock
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['background_jobs']['state'] == 'recovered_idle'
    assert result['background_jobs']['basis']
    assert not (state / 'bg_jobs.json').exists(), 'the guard must stay read-only; creation belongs to serve()'


def test_absent_store_fresh_install_keeps_uninitialized_basis(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = files(tmp_path, store=False)
    clean_scan(monkeypatch)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['background_jobs']['state'] == 'uninitialized_idle'
    assert not (state / 'bg_jobs.json.lock').exists(), 'the guard must not create files'


@pytest.mark.parametrize('scan', ['live-supervisor', 'incomplete', 'unknown-supervisors', 'malformed'])
def test_absent_store_with_ambiguous_supervisor_scan_refused(tmp_path, monkeypatch, scan):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    values = {
        'live-supervisor': {'complete': True, 'supervisors': [4242]},
        'incomplete': {'complete': False, 'supervisors': []},
        'unknown-supervisors': {'complete': True, 'supervisors': None},
        'malformed': None,
    }
    monkeypatch.setattr(control, '_native_background_processes', lambda: values[scan])
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']
    assert result['background_jobs']['state'] == 'unknown'


@pytest.mark.parametrize('registry_evidence', ['no-registry', 'corrupt', 'live-worker', 'unreconciled-row'])
def test_absent_store_requires_terminal_native_registry(tmp_path, monkeypatch, registry_evidence):
    from src import windows_app_restart as control
    from src import windows_restart_os as win
    state, workers, db = files(tmp_path, store=False)
    write_grants(state, {'g-released': {**expired_grant(), 'released_at': time.time()}})
    clean_scan(monkeypatch)
    proc = None
    if registry_evidence == 'corrupt':
        registry = initialize_registry(state)
        registry.path.write_text('garbage', encoding='utf-8')
    elif registry_evidence == 'live-worker':
        registry = initialize_registry(state)
        proc = registry.spawn('cookbook', 'fixture-live',
                              lambda: subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']))
    elif registry_evidence == 'unreconciled-row':
        # A crashed producer left a 'running' row whose supervisor is now dead:
        # snapshot derives 'unknown', never a fabricated completion.
        registry = initialize_registry(state)
        add_terminal_worker(registry)
        gone = expired_grant()
        data = json.loads(registry.path.read_text(encoding='utf-8'))
        generation = 'f' * 32
        data['workers'][generation] = {
            'id': generation, 'source': 'background', 'source_id': 'lost-job',
            'manager': {'pid': os.getpid(), 'start_token': win.identity(os.getpid())['start_token']},
            'state': 'running', 'created_at': time.time(),
            'identity': {'pid': gone['supervisor_pid'], 'start_token': gone['supervisor_token']},
            'exit_code': None, 'cancel_requested': False}
        registry.path.write_text(json.dumps(data), encoding='utf-8')
    try:
        result = control.file_guard(state, workers, db)
        assert result['clear'] is False
        assert 'unknown bg_jobs.json' in result['errors']
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)


@pytest.mark.parametrize('grant', ['live-supervisor', 'missing-token', 'missing-pid', 'malformed-store', 'non-dict-row'])
def test_absent_store_with_unaccounted_background_grant_refused(tmp_path, monkeypatch, grant):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    if grant == 'live-supervisor':
        write_grants(state, {'g1': live_grant()})
    elif grant == 'missing-token':
        write_grants(state, {'g1': {**live_grant(), 'supervisor_token': None}})
    elif grant == 'missing-pid':
        write_grants(state, {'g1': {**live_grant(), 'supervisor_pid': None}})
    elif grant == 'malformed-store':
        (state / 'containment_grants.json').write_text('{garbage', encoding='utf-8')
    elif grant == 'non-dict-row':
        write_grants(state, {'g1': 'not-a-dict'})
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']


def test_absent_store_with_released_live_grant_is_recovered(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    write_grants(state, {'g1': {**live_grant(), 'released_at': time.time()}})
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['background_jobs']['state'] == 'recovered_idle'


@pytest.mark.parametrize('remnant', ['no-exit', 'bad-exit', 'unknown-file', 'bad-job-id'])
def test_absent_store_with_unaccounted_job_remnants_refused(tmp_path, monkeypatch, remnant):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    if remnant == 'no-exit':
        remnant_job(state, exit_code=None)
    elif remnant == 'bad-exit':
        remnant_job(state, exit_code='not-an-int')
    elif remnant == 'unknown-file':
        remnant_job(state)
        (state / 'bg_jobs' / 'notes.txt').write_text('unaccounted', encoding='utf-8')
    elif remnant == 'bad-job-id':
        remnant_job(state, job_id='not-a-job-id')
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']


def test_symlinked_store_is_never_recovered_or_written(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    target = tmp_path / 'elsewhere.json'
    try:
        (state / 'bg_jobs.json').symlink_to(target)
    except OSError:
        pytest.skip('symlink creation requires privilege on this host')
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report['initialized'] is False
    assert not target.exists(), 'never write through an untrusted link'


def test_symlinked_bg_jobs_dir_refused(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    try:
        (state / 'bg_jobs').symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation requires privilege on this host')
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']


def test_live_grant_appearing_during_scan_refuses(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    def scan():
        write_grants(state, {'g-late': live_grant()})
        return {'complete': True, 'supervisors': []}
    monkeypatch.setattr(control, '_native_background_processes', scan)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']


def test_store_appearing_during_scan_refuses_and_is_preserved(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    def scan():
        (state / 'bg_jobs.json').write_text('{"sentinel": true}', encoding='utf-8')
        return {'complete': True, 'supervisors': []}
    monkeypatch.setattr(control, '_native_background_processes', scan)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {'sentinel': True}


def test_unaccounted_remnant_appearing_during_scan_refuses(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    def scan():
        remnant_job(state, exit_code=None)
        return {'complete': True, 'supervisors': []}
    monkeypatch.setattr(control, '_native_background_processes', scan)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']


@pytest.mark.parametrize('present', ['malformed', 'busy-row', 'terminal-row'])
def test_existing_store_semantics_unchanged(tmp_path, present):
    from src import windows_app_restart as control
    state, workers, db = files(tmp_path)
    if present == 'malformed':
        (state / 'bg_jobs.json').write_text('{garbage', encoding='utf-8')
        expected = 'unknown bg_jobs.json'
    elif present == 'busy-row':
        (state / 'bg_jobs.json').write_text(json.dumps({'j1': {'status': 'running'}}), encoding='utf-8')
        expected = 'busy or unknown bg_jobs.json'
    else:
        (state / 'bg_jobs.json').write_text(json.dumps({'j1': {'status': 'done'}}), encoding='utf-8')
        expected = None
    result = control.file_guard(state, workers, db)
    if expected is None:
        assert result['clear'] is True, result['errors']
        assert result['background_jobs']['state'] == 'recorded_idle'
    else:
        assert result['clear'] is False
        assert expected in result['errors']


def test_prepare_materializes_empty_store_only_under_proof(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    write_grants(state, {'g1': expired_grant()})
    remnant_job(state)
    before = {p.name: p.read_bytes() for p in (state / 'bg_jobs').iterdir()}
    grants_before = (state / 'containment_grants.json').read_bytes()
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report == {'state': 'initialized', 'initialized': True}
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {}
    assert {p.name: p.read_bytes() for p in (state / 'bg_jobs').iterdir()} == before, 'remnants are never touched'
    assert (state / 'containment_grants.json').read_bytes() == grants_before
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['background_jobs']['state'] == 'recorded_idle'


def test_prepare_never_overwrites_an_existing_store(tmp_path):
    from src import windows_app_restart as control
    state, workers, db = files(tmp_path)
    busy = json.dumps({'j1': {'status': 'running'}})
    (state / 'bg_jobs.json').write_text(busy, encoding='utf-8')
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report == {'state': 'present', 'initialized': False}
    assert (state / 'bg_jobs.json').read_text(encoding='utf-8') == busy


@pytest.mark.parametrize('ambiguity', ['live-supervisor', 'incomplete-scan', 'live-grant', 'no-registry'])
def test_prepare_refuses_creation_while_work_is_live_or_unproven(tmp_path, monkeypatch, ambiguity):
    from src import windows_app_restart as control
    if ambiguity == 'no-registry':
        state, workers, db = files(tmp_path, store=False)
        write_grants(state, {'g-released': {**expired_grant(), 'released_at': time.time()}})
        clean_scan(monkeypatch)
    else:
        state, workers, db = idle_state(tmp_path, monkeypatch)
        if ambiguity == 'live-supervisor':
            monkeypatch.setattr(control, '_native_background_processes',
                                lambda: {'complete': True, 'supervisors': [4242]})
        elif ambiguity == 'incomplete-scan':
            monkeypatch.setattr(control, '_native_background_processes',
                                lambda: {'complete': False, 'supervisors': None})
        elif ambiguity == 'live-grant':
            write_grants(state, {'g1': live_grant()})
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report == {'state': 'unproven', 'initialized': False}
    assert not (state / 'bg_jobs.json').exists(), 'an unproven idle state must never materialize an empty inventory'
    assert control.file_guard(state, workers, db)['clear'] is False


def test_prepare_store_appearing_after_outer_proof_wins(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    real = control._bg_jobs_recoverable
    def racing(state_dir):
        verdict = real(state_dir)
        (state / 'bg_jobs.json').write_text('{"sentinel": true}', encoding='utf-8')
        return verdict
    monkeypatch.setattr(control, '_bg_jobs_recoverable', racing)
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report == {'state': 'present', 'initialized': False}
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {'sentinel': True}


def test_prepare_store_appearing_during_inner_proof_wins(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    real = control._bg_jobs_recoverable
    calls = []
    def racing(state_dir):
        calls.append(1)
        if len(calls) > 1:
            (state / 'bg_jobs.json').write_text('{"sentinel": true}', encoding='utf-8')
        return real(state_dir)
    monkeypatch.setattr(control, '_bg_jobs_recoverable', racing)
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report['initialized'] is False
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {'sentinel': True}


def test_prepare_write_failure_is_nonfatal(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = idle_state(tmp_path, monkeypatch)
    def denied(*args, **kwargs):
        raise OSError('fixture denied')
    monkeypatch.setattr(control, 'atomic_write_json', denied)
    report = control.prepare_bg_jobs_inventory({'state_dir': str(state)})
    assert report == {'state': 'unproven', 'initialized': False}
    assert not (state / 'bg_jobs.json').exists()


async def lab_app(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            event = await receive()
            if event['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif event['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return
    elif scope['type'] == 'http':
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'content-type', b'application/json')]})
        await send({'type': 'http.response.body', 'body': b'{"status": "healthy"}'})


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def serve_config(tmp_path, state):
    from src import windows_restart_os as win
    parents = win.process_parents()
    return dict(root=str(ROOT), python=sys.executable, port=free_port(), gateway_port=free_port(),
                chroma_port=free_port(), state_dir=str(state), workers_dir=str(tmp_path / 'workers'),
                gateway_db=str(tmp_path / 'gateway.db'), control_dir=str(tmp_path / 'control'),
                launcher_pid=parents[os.getpid()],
                lab_application='tests.test_windows_app_restart_bgjobs:lab_app')


def run_serve_until_started(config, monkeypatch, started):
    import uvicorn
    from src import windows_app_restart as control
    captured = {}
    real_server = uvicorn.Server
    class CapturingServer(real_server):
        def __init__(self, cfg):
            super().__init__(cfg)
            captured['server'] = self
    monkeypatch.setattr(uvicorn, 'Server', CapturingServer)
    monkeypatch.setattr(control, 'protected_state', lambda cfg: [])
    for key, value in (('ODYSSEUS_DATA_DIR', config['state_dir']), ('APP_PORT', str(config['port'])),
                       ('ODYSSEUS_RESTART_LAB_GATEWAY_PORT', str(config['gateway_port'])),
                       ('ODYSSEUS_RESTART_LAB_DIR', config['state_dir'])):
        monkeypatch.setenv(key, value)
    async def main():
        task = asyncio.create_task(control.serve(config))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if task.done():
                await task
                raise AssertionError('serve exited before startup completed')
            if captured.get('server') is not None and captured['server'].started and started():
                break
            await asyncio.sleep(.05)
        else:
            raise AssertionError('serve did not start in time')
        captured['server'].should_exit = True
        await asyncio.wait_for(task, 30)
    asyncio.run(main())


def test_serve_initializes_absent_store_at_startup(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = files(tmp_path, store=False)
    add_terminal_worker(initialize_registry(state))
    clean_scan(monkeypatch)
    write_grants(state, {'g1': expired_grant()})
    remnant_job(state)
    config = serve_config(tmp_path, state)
    run_serve_until_started(config, monkeypatch, lambda: (state / 'bg_jobs.json').exists())
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {}
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['background_jobs']['state'] == 'recorded_idle'


def test_serve_leaves_store_absent_when_unproven_but_still_starts(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, workers, db = files(tmp_path, store=False)
    add_terminal_worker(initialize_registry(state))
    monkeypatch.setattr(control, '_native_background_processes',
                        lambda: {'complete': True, 'supervisors': [4242]})
    config = serve_config(tmp_path, state)
    run_serve_until_started(config, monkeypatch, lambda: True)
    assert not (state / 'bg_jobs.json').exists(), 'busy work must never be fabricated as idle'
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']


# Real launcher/uvicorn/pipe rehearsal: reuse the existing integration lab.
from tests.test_windows_app_restart_integration import lab  # noqa: F401,E402


def test_real_restart_recovers_absent_store(lab):
    from src.windows_app_restart import file_guard, restart
    config, old, _, _ = lab
    state = Path(config['state_dir'])
    # A released grant makes this a MATURE install: the uninitialized-idle
    # exception cannot apply, so recovery must use the new guarded proof.
    (state / 'containment_grants.json').write_text(
        json.dumps({'g-released': {'id': 'g-released', 'released_at': time.time()}}), encoding='utf-8')
    assert file_guard(state, config['workers_dir'], config['gateway_db'])['clear'] is True
    (state / 'bg_jobs.json').unlink()
    result = restart(config['control_dir'], timeout=30)
    assert result['ok'] is True, result
    assert result['identity'] != old['identity']
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {}
    guard = file_guard(state, config['workers_dir'], config['gateway_db'])
    assert guard['clear'] is True, guard['errors']
    assert guard['background_jobs']['state'] == 'recorded_idle'


def test_real_restart_refuses_absent_store_with_live_supervisor_image(lab):
    from core.platform_compat import native_python_command
    from src import windows_restart_os as win
    from src.windows_app_restart import restart
    config, old, _, _ = lab
    state = Path(config['state_dir'])
    (state / 'containment_grants.json').write_text(
        json.dumps({'g-released': {'id': 'g-released', 'released_at': time.time()}}), encoding='utf-8')
    command, env = native_python_command(
        [sys.executable, '-c', 'import time; time.sleep(120) # bg_worker.py'], None)
    fake = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        (state / 'bg_jobs.json').unlink()
        result = restart(config['control_dir'], timeout=5)
        assert result['ok'] is False
        assert result['state'] == 'blocked', result
        assert 'unknown bg_jobs.json' in result['guard']['errors']
        assert win.identity(old['identity']['pid']) == old['identity'], 'the app must keep running'
        assert not (state / 'bg_jobs.json').exists(), 'no empty inventory while a supervisor is live'
    finally:
        fake.terminate()
        fake.wait(timeout=10)
    result = restart(config['control_dir'], timeout=30)
    assert result['ok'] is True, result
    assert json.loads((state / 'bg_jobs.json').read_text(encoding='utf-8')) == {}


@pytest.mark.parametrize('name', ['bg_jobs.json', 'bg_jobs', 'containment_grants.json'])
def test_fresh_install_with_dangling_artifact_symlink_refused(tmp_path, monkeypatch, name):
    from src import windows_app_restart as control
    state, workers, db = files(tmp_path, store=False)
    clean_scan(monkeypatch)
    target = tmp_path / 'elsewhere-target'
    try:
        if name == 'bg_jobs':
            (state / name).symlink_to(target, target_is_directory=True)
        else:
            (state / name).symlink_to(target)
    except OSError:
        pytest.skip('symlink creation requires privilege on this host')
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown bg_jobs.json' in result['errors']
