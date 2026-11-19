"""Actual Windows launcher/Uvicorn/pipe/process tests; no production app boot."""
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Real Windows process/pipe rehearsal')

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(function, seconds=15):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            value = function()
            if value:
                return value
        except Exception as exc:
            last = exc
        time.sleep(.05)
    raise AssertionError(f'Condition did not become true: {type(last).__name__}')


@pytest.fixture
def lab(tmp_path, request):
    # Never invoke the old installer accidentally when testing an absent mode.
    assert '[switch]$RunOnly' in (ROOT / 'launch-windows.ps1').read_text(), 'run-only mode missing'
    from src.windows_app_restart import health, launcher_argv, request_control
    from src import windows_restart_os as win
    state = tmp_path / 'data'
    state.mkdir()
    (state / 'cookbook_state.json').write_text('{"tasks": []}')
    (state / 'bg_jobs.json').write_text('{}')
    workers = tmp_path / 'workers'
    workers.mkdir()
    db = tmp_path / 'gateway.db'
    with sqlite3.connect(db) as con:
        con.execute('create table session_turn_leases(expires_at real)')
    config = dict(root=str(ROOT), python=sys.executable, port=free_port(), gateway_port=free_port(),
                  chroma_port=free_port(), state_dir=str(state), workers_dir=str(workers),
                  gateway_db=str(db), control_dir=str(tmp_path / 'control'),
                  lab_application='tests.helpers.windows_restart_fixture:app')
    if getattr(request, 'param', '') == 'weak-secret':
        directory = Path(config['control_dir'])
        directory.mkdir()
        file = directory / 'secret.key'
        file.write_bytes(b'old-fixture-key')
        descriptor = win.C.c_void_p()
        win.checked(win._sd(f'D:P(A;;GA;;;{win.user_sid()})(A;;GR;;;WD)', 1,
                            win.C.byref(descriptor), None))
        try:
            win.checked(win._set(str(file), 0x80000004, descriptor))
        finally:
            win._free(descriptor)
    launches = []
    stream = (tmp_path / 'launcher.log').open('ab')
    sidecar = subprocess.Popen([sys.executable, str(ROOT / 'tests/helpers/windows_restart_fixture.py'),
                               '--port', str(config['chroma_port'])], stdout=stream, stderr=stream,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    wait_for(lambda: health(config['chroma_port'], '/api/v2/heartbeat', kind='chroma'))
    env = {key: value for key, value in os.environ.items() if not key.startswith(('ODYSSEUS_', 'HERMES_'))}
    def launch():
        child = subprocess.Popen(launcher_argv(config), stdout=stream, stderr=stream,
                                 stdin=subprocess.DEVNULL, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        launches.append(child)
        try:
            wait_for(lambda: request_control(config['control_dir'], 'status'))
        except Exception:
            stream.flush()
            raise AssertionError((tmp_path / 'launcher.log').read_text(errors='replace')) from None
        return child
    if getattr(request, 'param', '') == 'legacy':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(ROOT / 'tests/helpers/windows_restart_legacy.ps1'), '-Python', sys.executable,
                   '-Port', str(config['port']), '-GatewayPort', str(config['gateway_port']),
                   '-ChromaPort', str(config['chroma_port']), '-SidecarPid', str(sidecar.pid),
                   '-StateDirectory', str(state)]
        child = subprocess.Popen(command, stdout=stream, stderr=stream, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        launches.append(child)
        wait_for(lambda: health(config['port']))
        from src.windows_app_restart import protected_state
        original = {'identity': win.identity(win.listener_pid(config['port'])),
                    'launcher': win.identity(child.pid), 'protected': protected_state(config), 'config': config}
    else:
        launch()
        original = json.loads((Path(config['control_dir']) / 'app.json').read_text())
    try:
        yield config, original, launch, tmp_path
    finally:
        (state / 'shutdown-hold').unlink(missing_ok=True)
        (state / 'cookbook_state.json').write_text('{"tasks": []}')
        (state / 'bg_jobs.json').write_text('{}')
        with sqlite3.connect(db) as con:
            con.execute('delete from session_turn_leases')
        for file in workers.glob('*.pid'):
            file.unlink()
        try:
            from src.windows_app_restart import restart
            restart(config['control_dir'], timeout=5, start=False)
        except Exception:
            pass
        try:
            connection = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=2)
            connection.request('POST', '/fixture-exit')
            connection.getresponse().read()
            connection.close()
        except OSError:
            pass
        for port in (config['gateway_port'], config['chroma_port']):
            try:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                connection.request('POST', '/fixture-stop')
                connection.getresponse().read()
                connection.close()
            except OSError:
                pass
        for child in launches + [sidecar]:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()  # Direct handles of this lab's launchers only.
                child.wait(timeout=5)
        stream.close()


def test_real_launcher_restart_preserves_gateway_connection_and_sidecar(lab):
    from src.windows_app_restart import request_control, restart
    from src import windows_restart_os as win
    config, old, _, root = lab
    with sqlite3.connect(config['gateway_db']) as con:
        con.execute('insert into session_turn_leases values (?)', (time.time() + 60,))
    assert restart(config['control_dir'], timeout=2)['state'] == 'blocked'
    assert win.identity(old['identity']['pid']) == old['identity']
    with sqlite3.connect(config['gateway_db']) as con:
        con.execute('delete from session_turn_leases')
    state = Path(config['state_dir']) / 'cookbook_state.json'
    state.write_text('{"tasks":[{"status":"running","type":"download"}]}')
    assert restart(config['control_dir'], timeout=2)['state'] == 'blocked'
    state.write_text('{"tasks":[]}')
    worker = Path(config['workers_dir']) / 'unknown.pid'
    worker.write_text('invalid')
    assert restart(config['control_dir'], timeout=2)['state'] == 'blocked'
    worker.unlink()
    for action in ('spawn-worker', 'clear-worker'):
        temporary = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=3)
        temporary.request('POST', '/fixture-' + action)
        temporary.getresponse().read()
        temporary.close()
        if action == 'spawn-worker':
            assert restart(config['control_dir'], timeout=2)['state'] == 'blocked'
    bad = request_control(config['control_dir'], 'stop', key_override=b'not-the-secret')
    assert bad['ok'] is False
    wrong = request_control(config['control_dir'], 'stop', identity_override={'pid': 1, 'start_token': 'wrong'})
    assert wrong['ok'] is False
    connection = http.client.HTTPConnection('127.0.0.1', config['gateway_port'], timeout=3)
    connection.request('GET', '/health')
    assert connection.getresponse().status == 200
    # Drain body before reusing the same HTTP/1.1 connection.
    connection.close()
    connection = http.client.HTTPConnection('127.0.0.1', config['gateway_port'], timeout=3)
    connection.request('GET', '/health')
    connection.getresponse().read()
    socket_before = connection.sock
    result = restart(config['control_dir'], timeout=15)
    assert result['ok'] is True, result
    assert result['identity'] != old['identity']
    assert result['protected_unchanged'] is True
    connection.request('GET', '/health')
    assert connection.getresponse().read() == b'{"ok":true}'
    assert connection.sock is socket_before
    connection.close()
    (root / 'result.json').write_text(json.dumps(result, indent=2))


def test_graceful_timeout_never_starts_replacement_or_terminates_app(lab):
    from src.windows_app_restart import restart
    from src import windows_restart_os as win
    config, old, _, _ = lab
    hold = Path(config['state_dir']) / 'shutdown-hold'
    hold.touch()
    result = restart(config['control_dir'], timeout=.5)
    assert result['state'] == 'shutdown-timeout'
    assert result['forced'] is False
    assert result['replacement_started'] is False
    assert win.identity(old['identity']['pid']) == old['identity']
    hold.unlink()
    wait_for(lambda: win.identity(old['identity']['pid']) is None)


@pytest.mark.parametrize('lab', ['weak-secret'], indirect=True)
def test_old_permissive_key_file_is_replaced_with_private_acl(lab):
    from src import windows_restart_os as win
    config, _, _, _ = lab
    env = dict(os.environ, RESTART_KEY_ACL=str(Path(config['control_dir']) / 'secret.key'))
    output = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
        '@((Get-Acl -LiteralPath $env:RESTART_KEY_ACL).Access | ForEach-Object '
        '{ $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value }) | ConvertTo-Json -Compress'], env=env)
    assert set(json.loads(output)) == {win.user_sid(), 'S-1-5-18'}


@pytest.mark.parametrize('lab', ['legacy'], indirect=True)
def test_one_time_legacy_transition_only_terminates_pinned_lab_app_chain(lab):
    from ctypes import byref
    from ctypes import wintypes as W
    from src.windows_app_restart import descendants, full_guard, verify_protected
    from src import windows_restart_os as win
    config, old, launch, root = lab
    assert not (Path(config['control_dir']) / 'app.json').exists(), 'legacy has no control hook'
    assert full_guard(config, old['identity'], old['protected'])['clear'] is True
    parents = win.process_parents()
    app_pid, launcher_pid = old['identity']['pid'], old['launcher']['pid']
    assert old['protected'][0]['pid'] in descendants(app_pid, parents), 'gateway is a real app descendant'
    targets = [launcher_pid]
    if parents[app_pid] != launcher_pid:
        targets.append(parents[app_pid])
    targets.append(app_pid)
    assert set(targets) <= descendants(launcher_pid, parents)
    assert not set(targets) & {row['pid'] for row in old['protected']}
    # Force exists ONLY in this opt-in legacy LAB rehearsal, never in the
    # operational coordinator. Pin all handles before terminating our family.
    pinned = []
    try:
        for pid in targets:
            expected = win.identity(pid)
            handle = win.checked(win._open(0x100001 | 0x1000, False, pid))
            times = [W.FILETIME() for _ in range(4)]
            win.checked(win._times(handle, *(byref(value) for value in times)))
            token = f'win32:{(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}'
            assert expected == {'pid': pid, 'start_token': token}
            pinned.append(handle)
        assert full_guard(config, old['identity'], old['protected'])['clear'] is True
        terminate = win.api(win.K, 'TerminateProcess', W.BOOL, W.HANDLE, W.UINT)
        wait = win.api(win.K, 'WaitForSingleObject', W.DWORD, W.HANDLE, W.DWORD)
        for handle in pinned:
            code = W.DWORD()
            win.checked(win._exit(handle, byref(code)))
            if code.value == 259 and not terminate(handle, 0):
                # A venv redirector may exit while its pinned app exits. A
                # dead handle is not an access-control failure or a new PID.
                error = win.C.get_last_error()
                win.checked(win._exit(handle, byref(code)))
                if code.value == 259:
                    raise win.C.WinError(error)
            assert wait(handle, 3000) == 0
    finally:
        for handle in pinned:
            win.close(handle)
    verify_protected(old['protected'])
    launch()
    current = json.loads((Path(config['control_dir']) / 'app.json').read_text())
    assert current['identity'] != old['identity']
    assert current['protected'] == old['protected']
    (root / 'legacy-transition-result.json').write_text(json.dumps({
        'lab_only': True, 'old_identity': old['identity'], 'new_identity': current['identity'],
        'protected_unchanged': True, 'targeted_app_chain': targets, 'tree_kill': False}, indent=2))


def test_rebaseline_recovers_cooperative_restart_after_legitimate_gateway_restart(lab):
    """Live-incident rehearsal: the gateway legitimately restarts, stop refuses
    fail-closed, an authenticated operator rebaseline refreshes ONLY protected,
    and cooperative restart works again -- no tree-stop, no manual relaunch."""
    from src.windows_app_restart import health, request_control, restart, verify_protected
    from src import windows_restart_os as win
    config, old, _, root = lab
    directory = Path(config['control_dir'])
    gateway_port = config['gateway_port']
    old_gateway = old['protected'][0]
    assert old_gateway['kind'] == 'gateway'
    old_identity = {'pid': old_gateway['pid'], 'start_token': old_gateway['start_token']}
    # 1. Legitimate shared-service restart: retire the gateway sidecar, launch a
    #    fresh incarnation on the same port (real pid/start_token change).
    connection = http.client.HTTPConnection('127.0.0.1', gateway_port, timeout=3)
    connection.request('POST', '/fixture-stop')
    connection.getresponse().read()
    connection.close()
    wait_for(lambda: win.identity(old_gateway['pid']) != old_identity)
    stream = (root / 'replacement-gateway.log').open('ab')
    replacement = subprocess.Popen(
        [sys.executable, str(ROOT / 'tests/helpers/windows_restart_fixture.py'), '--port', str(gateway_port)],
        stdout=stream, stderr=stream, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        wait_for(lambda: health(gateway_port, '/api/health', kind='gateway'))
        # The venv python.exe may be a redirector: Popen.pid is not necessarily
        # the interpreter that owns the listener. Pin the REAL listener identity.
        new_pid = win.listener_pid(gateway_port)
        assert new_pid != old_gateway['pid']
        assert win.identity(new_pid) is not None
        # 2. Existing fail-closed protection is intact: verify + stop refuse.
        with pytest.raises(RuntimeError, match='Protected service identity or health changed'):
            verify_protected(old['protected'])
        blocked = request_control(directory, 'stop', timeout=10)
        assert blocked['ok'] is False and blocked['state'] == 'blocked', blocked
        assert 'Protected service identity or health changed' in blocked['guard']['errors']
        # 3. A tampered on-disk receipt is refused even with healthy services.
        original_bytes = (directory / 'app.json').read_bytes()
        forged = json.loads(original_bytes)
        forged['protected'][0] = {**forged['protected'][1], 'port': gateway_port,
                                  'path': '/api/health', 'kind': 'gateway'}
        (directory / 'app.json').write_text(json.dumps(forged, indent=2), encoding='utf-8')
        tampered = subprocess.run([sys.executable, str(ROOT / 'scripts/windows-app-control.py'),
                                   'rebaseline', '--control-dir', str(directory), '--timeout', '10'],
                                  capture_output=True, text=True, timeout=30,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        assert tampered.returncode == 1, tampered.stdout + tampered.stderr
        assert 'does not match the live control state' in json.loads(tampered.stdout)['error']
        (directory / 'app.json').write_bytes(original_bytes)
        # 4. Sanctioned operator recovery via the real CLI + pipe + MAC.
        recovered = subprocess.run([sys.executable, str(ROOT / 'scripts/windows-app-control.py'),
                                    'rebaseline', '--control-dir', str(directory), '--timeout', '20'],
                                   capture_output=True, text=True, timeout=40,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        result = json.loads(recovered.stdout)
        assert result['ok'] is True and result['state'] == 'rebaselined', result
        assert result['forced'] is False
        assert result['protected'][0]['pid'] == new_pid
        assert result['protected'][1] == old['protected'][1]  # untouched service kept
        assert result['previous_protected'] == old['protected']
        disk = json.loads((directory / 'app.json').read_text(encoding='utf-8'))
        assert disk['protected'] == result['protected']
        assert {k: v for k, v in disk.items() if k != 'protected'} == {k: v for k, v in old.items() if k != 'protected'}
        verify_protected(disk['protected'])
        log = [json.loads(line) for line in (directory / 'rebaseline.log').read_text(encoding='utf-8').splitlines()]
        assert [row['ok'] for row in log] == [False, True]
        assert log[1]['previous_protected'] == old['protected']
        assert log[1]['protected'][0]['pid'] == new_pid
        # 5. Cooperative restart is unblocked: full stop + relaunch, no force.
        outcome = restart(directory, timeout=30)
        assert outcome['ok'] is True, outcome
        assert outcome['state'] == 'healthy'
        assert outcome['protected_unchanged'] is True
        assert outcome['identity'] != old['identity']
        (root / 'rebaseline-recovery-result.json').write_text(json.dumps(
            {'old_gateway': old_gateway, 'new_gateway_pid': new_pid,
             'rebaseline': result['state'], 'restart': outcome['state']}, indent=2), encoding='utf-8')
    finally:
        try:
            connection = http.client.HTTPConnection('127.0.0.1', gateway_port, timeout=3)
            connection.request('POST', '/fixture-stop')
            connection.getresponse().read()
            connection.close()
        except OSError:
            pass
        try:
            replacement.wait(timeout=5)
        except subprocess.TimeoutExpired:
            replacement.terminate()  # Direct handle of this lab's own replacement only.
            replacement.wait(timeout=5)
        # The redirector may exit before its interpreter child: verify the port
        # is actually released; if not, pin and terminate ONLY that lab child.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', gateway_port)) != 0:
                    break
            time.sleep(.05)
        else:
            from ctypes import byref
            from ctypes import wintypes as W
            listener = win.listener_pid(gateway_port)
            handle = win.checked(win._open(0x100001 | 0x1000, False, listener))
            try:
                terminate = win.api(win.K, 'TerminateProcess', W.BOOL, W.HANDLE, W.UINT)
                wait = win.api(win.K, 'WaitForSingleObject', W.DWORD, W.HANDLE, W.DWORD)
                assert terminate(handle, 0)
                assert wait(handle, 5000) == 0
            finally:
                win.close(handle)
        stream.close()
