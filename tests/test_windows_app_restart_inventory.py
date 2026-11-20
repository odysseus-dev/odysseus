"""Durable worker-receipt inventory: migration, guarded recovery, fail-closed negatives."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Native Windows control infrastructure')

ROOT = Path(__file__).resolve().parents[1]
BS = chr(92)  # literal backslash without source-escaping ambiguity


@pytest.fixture(autouse=True)
def isolated_legacy(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    legacy = tmp_path / 'legacy-temp' / 'odysseus-tmux'
    monkeypatch.setattr(control, 'legacy_workers_dir', lambda: legacy, raising=False)
    return legacy


def files(tmp_path):
    state = tmp_path / 'data'
    state.mkdir(exist_ok=True)
    (state / 'cookbook_state.json').write_text('{"tasks": []}', encoding='utf-8')
    (state / 'bg_jobs.json').write_text('{}', encoding='utf-8')
    db = tmp_path / 'gateway.db'
    with sqlite3.connect(db) as con:
        con.execute('create table session_turn_leases(expires_at real)')
    return state, db


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
    monkeypatch.setattr(control, '_worker_process_scan', lambda: {'complete': True, 'processes': [
        {'pid': os.getpid(), 'name': 'python.exe', 'command_line': sys.executable + ' -m pytest'}]})


def live_receipt(directory, name='cookbook-live'):
    from src import windows_restart_os as win
    directory.mkdir(parents=True, exist_ok=True)
    identity = win.identity(os.getpid())
    (directory / (name + '.pid')).write_text(str(os.getpid()), encoding='utf-8')
    (directory / (name + '.pid.token')).write_text(identity['start_token'], encoding='utf-8')


def idle_proof_fixture(tmp_path, monkeypatch):
    state, db = files(tmp_path)
    add_terminal_worker(initialize_registry(state))
    clean_scan(monkeypatch)
    return state, db, tmp_path / 'workers'


def test_missing_inventory_without_evidence_still_refused(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    clean_scan(monkeypatch)
    result = control.file_guard(state, tmp_path / 'workers', db)
    assert result['clear'] is False
    assert 'unknown worker inventory' in result['errors']
    assert result['worker_inventory']['state'] == 'unknown'


def test_missing_inventory_recovered_with_complete_idle_proof(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['worker_inventory']['state'] == 'recovered_idle'
    assert result['worker_inventory']['basis']
    assert result['worker_receipts'] == []
    assert not workers.exists(), 'the guard must stay read-only; creation belongs to serve()'


@pytest.mark.parametrize('style', ['backslash', 'posix', 'mixed-case'])
def test_missing_inventory_with_live_worker_process_refused(tmp_path, monkeypatch, style):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    path = str(workers) + BS + 'cookbook-x.sh' if style == 'backslash' else workers.as_posix() + '/cookbook-x.sh'
    if style == 'mixed-case':
        path = path.upper()
    monkeypatch.setattr(control, '_worker_process_scan', lambda: {'complete': True, 'processes': [
        {'pid': os.getpid(), 'name': 'python.exe', 'command_line': sys.executable + ' -m pytest'},
        {'pid': 4242, 'name': 'bash.exe', 'command_line': 'bash ' + path}]})
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown worker inventory' in result['errors']


@pytest.mark.parametrize('scan', ['incomplete', 'no-processes', 'malformed-row', 'query-failure'])
def test_missing_inventory_with_ambiguous_scan_refused(tmp_path, monkeypatch, scan):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    values = {
        'incomplete': {'complete': False, 'processes': []},
        'no-processes': {'complete': True, 'processes': None},
        'malformed-row': {'complete': True, 'processes': [{'pid': 4242, 'name': 'bash.exe'}]},
        'query-failure': {'complete': False, 'processes': None},
    }
    monkeypatch.setattr(control, '_worker_process_scan', lambda: values[scan])
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown worker inventory' in result['errors']


@pytest.mark.parametrize('registry_evidence', ['no-registry', 'corrupt', 'live-worker'])
def test_missing_inventory_requires_terminal_native_registry(tmp_path, monkeypatch, registry_evidence):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    clean_scan(monkeypatch)
    proc = None
    if registry_evidence == 'corrupt':
        registry = initialize_registry(state)
        registry.path.write_text('garbage', encoding='utf-8')
    elif registry_evidence == 'live-worker':
        registry = initialize_registry(state)
        proc = registry.spawn('cookbook', 'fixture-live',
                              lambda: subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']))
    try:
        result = control.file_guard(state, tmp_path / 'workers', db)
        assert result['clear'] is False
        assert 'unknown worker inventory' in result['errors']
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)


def test_receipts_appearing_during_proof_refuse(tmp_path, monkeypatch, isolated_legacy):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    def scan():
        live_receipt(isolated_legacy, name='cookbook-late')
        return {'complete': True, 'processes': [
            {'pid': os.getpid(), 'name': 'python.exe', 'command_line': sys.executable + ' -m pytest'}]}
    monkeypatch.setattr(control, '_worker_process_scan', scan)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False


def test_missing_inventory_with_live_legacy_receipt_refused(tmp_path, monkeypatch, isolated_legacy):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    live_receipt(isolated_legacy)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'live or unknown worker receipt' in result['errors']


def test_missing_inventory_with_gone_legacy_receipt_is_recorded(tmp_path, monkeypatch, isolated_legacy):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    isolated_legacy.mkdir(parents=True)
    (isolated_legacy / 'cookbook-old.pid').write_text('321', encoding='utf-8')
    monkeypatch.setattr(control.win, 'identity', lambda pid: None)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['worker_inventory']['state'] == 'recorded'
    assert result['worker_receipts'][0]['state'] == 'gone'


def test_missing_inventory_with_malformed_legacy_receipt_refused(tmp_path, monkeypatch, isolated_legacy):
    from src import windows_app_restart as control
    state, db, workers = idle_proof_fixture(tmp_path, monkeypatch)
    isolated_legacy.mkdir(parents=True)
    (isolated_legacy / 'bad.pid').write_text('not-a-pid', encoding='utf-8')
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'live or unknown worker receipt' in result['errors']


def test_existing_empty_inventory_semantics_unchanged(tmp_path):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    workers = tmp_path / 'workers'
    workers.mkdir()
    result = control.file_guard(state, workers, db)
    assert result['clear'] is True, result['errors']
    assert result['worker_inventory']['state'] == 'recorded'


def test_existing_inventory_live_receipt_refused(tmp_path):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    workers = tmp_path / 'workers'
    live_receipt(workers)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'live or unknown worker receipt' in result['errors']


def test_legacy_live_receipt_refused_even_when_primary_present(tmp_path, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    workers = tmp_path / 'workers'
    workers.mkdir()
    live_receipt(isolated_legacy)
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'live or unknown worker receipt' in result['errors']


def test_symlink_inventory_location_refused(tmp_path, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    target = tmp_path / 'elsewhere'
    target.mkdir()
    workers = tmp_path / 'workers'
    try:
        workers.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation requires privilege on this host')
    result = control.file_guard(state, workers, db)
    assert result['clear'] is False
    assert 'unknown worker inventory' in result['errors']


def test_scan_marker_matching_is_conservative_and_self_safe(tmp_path):
    from src import windows_app_restart as control
    workers = tmp_path / 'worker_receipts'
    legacy = tmp_path / 'odysseus-tmux'
    directories = (workers, legacy)
    def row(line, pid=4242):
        return {'pid': pid, 'name': 'bash.exe', 'command_line': line}
    app_line = f'python.exe scripts{BS}windows-app-control.py run --state-dir {tmp_path} --workers-dir {workers}'
    launcher_line = f'powershell.exe -File launch-windows.ps1 -WorkersDirectory {workers}'
    assert control._worker_scan_matches([row(app_line), row(launcher_line)], directories) == []
    assert control._worker_scan_matches([row('bash ' + str(workers) + BS + 'cookbook-a.sh')], directories) == [4242]
    assert control._worker_scan_matches([row('bash ' + workers.as_posix() + '/cookbook-a_run.sh')], directories) == [4242]
    assert control._worker_scan_matches([row('cmd.exe /c ' + str(legacy) + BS + 'cookbook-b.cmd')], directories) == [4242]
    assert control._worker_scan_matches([row('python.exe src' + BS + 'containment_worker.py --x')], directories) == [4242]
    assert control._worker_scan_matches([row('python.exe -m pytest')], directories) == []
    assert control._worker_scan_matches([{'pid': 1, 'name': 'bash.exe'}], directories) is None


def test_prepare_migrates_legacy_receipts_by_copy(tmp_path, monkeypatch, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    live_receipt(isolated_legacy)
    (isolated_legacy / 'cookbook-live.pid.ready').write_text('', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in isolated_legacy.iterdir()}
    monkeypatch.delenv('ODYSSEUS_WORKERS_DIR', raising=False)
    result = control.prepare_worker_inventory({'state_dir': str(state)})
    target = state / 'worker_receipts'
    assert result == str(target.resolve())
    assert os.environ['ODYSSEUS_WORKERS_DIR'] == result
    for name, data in before.items():
        assert (target / name).read_bytes() == data, 'receipt sidecars must migrate intact'
        assert (isolated_legacy / name).read_bytes() == data, 'migration must never delete legacy evidence'
    guard = control.file_guard(state, result, db)
    assert guard['clear'] is False, 'a migrated live receipt must still refuse restart'


def test_prepare_never_overwrites_an_existing_receipt(tmp_path, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    live_receipt(isolated_legacy, name='cookbook-same')
    target = state / 'worker_receipts'
    target.mkdir(parents=True)
    (target / 'cookbook-same.pid').write_text('999999', encoding='utf-8')
    control.prepare_worker_inventory({'state_dir': str(state)})
    assert (target / 'cookbook-same.pid').read_text() == '999999'


def test_prepare_creates_empty_inventory_only_under_proof(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    add_terminal_worker(initialize_registry(state))
    clean_scan(monkeypatch)
    result = control.prepare_worker_inventory({'state_dir': str(state)})
    target = state / 'worker_receipts'
    assert result == str(target.resolve())
    assert target.is_dir() and not any(target.iterdir())
    assert control.file_guard(state, result, db)['clear'] is True


def test_prepare_refuses_creation_while_a_worker_is_live(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    add_terminal_worker(initialize_registry(state))
    workers = state / 'worker_receipts'
    monkeypatch.setattr(control, '_worker_process_scan', lambda: {'complete': True, 'processes': [
        {'pid': 4242, 'name': 'bash.exe', 'command_line': 'bash ' + str(workers) + BS + 'cookbook-x.sh'}]})
    result = control.prepare_worker_inventory({'state_dir': str(state)})
    assert not workers.exists(), 'an unproven idle state must never materialize an empty inventory'
    assert control.file_guard(state, result, db)['clear'] is False


def test_prepare_migration_failure_falls_back_to_legacy(tmp_path, monkeypatch, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    live_receipt(isolated_legacy)
    def denied(*args, **kwargs):
        raise OSError('fixture denied')
    monkeypatch.setattr(control.shutil, 'copy2', denied)
    result = control.prepare_worker_inventory({'state_dir': str(state)})
    assert result == str(isolated_legacy.resolve())
    assert os.environ['ODYSSEUS_WORKERS_DIR'] == result
    assert control.file_guard(state, result, db)['clear'] is False, 'fallback must keep the live receipt visible'


def test_prepare_explicit_legacy_override_respected(tmp_path, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    isolated_legacy.mkdir(parents=True)
    result = control.prepare_worker_inventory({'state_dir': str(state), 'workers_dir': str(isolated_legacy)})
    assert result == str(isolated_legacy.resolve())
    assert not (state / 'worker_receipts').exists()


def test_prepare_symlink_target_is_not_trusted(tmp_path, isolated_legacy):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    isolated_legacy.mkdir(parents=True)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    link = tmp_path / 'linked-workers'
    try:
        link.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation requires privilege on this host')
    result = control.prepare_worker_inventory({'state_dir': str(state), 'workers_dir': str(link)})
    assert result == str(isolated_legacy.resolve())
    assert not any(elsewhere.iterdir()), 'never migrate into an untrusted location'


def _load_control_script():
    spec = importlib.util.spec_from_file_location('windows_app_control', ROOT / 'scripts' / 'windows-app-control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_script_default_workers_dir_is_durable(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    captured = {}
    async def fake_serve(config):
        captured.update(config)
    monkeypatch.setattr(control, 'serve', fake_serve)
    module = _load_control_script()
    monkeypatch.setattr(sys, 'argv', ['windows-app-control.py', 'run', '--control-dir', str(tmp_path / 'c'),
                                      '--state-dir', str(state), '--launcher-pid', '123'])
    assert module.main() == 0
    assert captured['workers_dir'] == str((state / 'worker_receipts').resolve())


def test_control_script_explicit_workers_dir_respected(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, db = files(tmp_path)
    captured = {}
    async def fake_serve(config):
        captured.update(config)
    monkeypatch.setattr(control, 'serve', fake_serve)
    module = _load_control_script()
    explicit = tmp_path / 'custom-workers'
    monkeypatch.setattr(sys, 'argv', ['windows-app-control.py', 'run', '--control-dir', str(tmp_path / 'c'),
                                      '--state-dir', str(state), '--workers-dir', str(explicit),
                                      '--launcher-pid', '123'])
    assert module.main() == 0
    assert captured['workers_dir'] == str(explicit.resolve())


def test_receipt_writers_follow_published_inventory_location(tmp_path):
    probe = tmp_path / 'probe_tmux_dir.py'
    probe.write_text('from routes.shell_routes import TMUX_LOG_DIR\nprint(TMUX_LOG_DIR)\n', encoding='utf-8')
    override = tmp_path / 'durable' / 'worker_receipts'
    temp = tmp_path / 'probe-temp'
    temp.mkdir()
    base = dict(os.environ, TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp),
                ODYSSEUS_DATA_DIR=str(tmp_path / 'data'),
                PYTHONPATH=str(ROOT))
    base.pop('ODYSSEUS_WORKERS_DIR', None)
    env_override = dict(base, ODYSSEUS_WORKERS_DIR=str(override))
    published = subprocess.run([sys.executable, str(probe)], cwd=ROOT, env=env_override,
                               capture_output=True, text=True, timeout=120,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    assert published.returncode == 0, published.stderr
    assert published.stdout.strip() == str(override)
    legacy = subprocess.run([sys.executable, str(probe)], cwd=ROOT, env=base,
                            capture_output=True, text=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert legacy.returncode == 0, legacy.stderr
    assert legacy.stdout.strip() == str(temp / 'odysseus-tmux')
