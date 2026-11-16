"""Cooperative Windows app control. No kill API, console signal or force fallback."""
from __future__ import annotations

import asyncio
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request

from core.atomic_io import atomic_write_json, atomic_write_text
from src import windows_restart_os as win

TERMINAL = {'done', 'completed', 'finished', 'error', 'failed', 'stopped', 'cancelled', 'canceled', 'ended', 'killed'}


def file_guard(state_dir, workers_dir, gateway_db):
    errors = []
    try:
        with closing(sqlite3.connect(Path(gateway_db).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            active = db.execute('select count(*) from session_turn_leases where expires_at > ?', (time.time(),)).fetchone()[0]
            if active:
                errors.append('active Hermes turns')
    except Exception:
        errors.append('unknown Hermes turn inventory')
    for filename in ('cookbook_state.json', 'bg_jobs.json'):
        try:
            data = json.loads((Path(state_dir) / filename).read_text(encoding='utf-8'))
            records = data.get('tasks') if filename == 'cookbook_state.json' else data
            if isinstance(records, dict):
                records = list(records.values())
            if not isinstance(records, list):
                raise ValueError('invalid inventory')
            if any(not isinstance(row, dict) or str(row.get('status', '')).lower() not in TERMINAL for row in records):
                errors.append('busy or unknown ' + filename)
        except Exception:
            errors.append('unknown ' + filename)
    try:
        directory = Path(workers_dir)
        if not directory.is_dir():
            raise ValueError('missing worker inventory')
        for file in directory.glob('*.pid'):
            pid = int(file.read_text(encoding='utf-8').strip())
            if pid <= 0 or win.identity(pid) is not None:
                errors.append('live or unknown worker receipt')
    except Exception:
        errors.append('unknown worker inventory')
    return {'clear': not errors, 'errors': errors}


def health(port, path='/api/health', *, kind='app'):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Health probes require a numeric local port')
    paths = {'app': '/api/health', 'gateway': '/api/health', 'chroma': '/api/v2/heartbeat'}
    if kind not in paths or path != paths[kind]:
        raise ValueError('Unknown local health path')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=1) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                return False
            raw = response.read(65537)
            if len(raw) > 65536:
                return False
            body = json.loads(raw)
        if not isinstance(body, dict):
            return False
        if kind == 'app':
            return body.get('status') == 'healthy'
        if kind == 'gateway':
            return (body.get('ok') is True and type(body.get('auth_required')) is bool
                    and isinstance(body.get('displayVersion'), str) and bool(body['displayVersion']))
        value = body.get('nanosecond heartbeat')
        return type(value) is int and value > 0
    except (OSError, ValueError, RecursionError):
        return False


def protected_state(config):
    result = []
    for port, path, kind in ((config['gateway_port'], '/api/health', 'gateway'),
                             (config['chroma_port'], '/api/v2/heartbeat', 'chroma')):
        if not health(port, path, kind=kind):
            raise RuntimeError('Shared service is not healthy')
        value = win.identity(win.listener_pid(port))
        if not value:
            raise RuntimeError('Shared listener identity unavailable')
        result.append({'port': port, 'path': path, 'kind': kind, **value})
    return result


def verify_protected(records):
    for value in records:
        if (win.identity(value['pid']) != {k: value[k] for k in ('pid', 'start_token')}
                or win.listener_pid(value['port']) != value['pid']
                or not health(value['port'], value['path'], kind=value.get('kind'))):
            raise RuntimeError('Protected service identity or health changed')


def descendants(root, parents):
    found = {root}
    while True:
        more = {pid for pid, parent in parents.items() if parent in found} - found
        if not more:
            return found
        found.update(more)


def full_guard(config, app_identity, protected):
    result = file_guard(config['state_dir'], config['workers_dir'], config['gateway_db'])
    try:
        if win.identity(app_identity['pid']) != app_identity or win.listener_pid(config['port']) != app_identity['pid']:
            raise RuntimeError('App identity/listener mismatch')
        verify_protected(protected)
        parents = win.process_parents()
        permitted = set()
        for service in protected:
            cursor = service['pid']
            seen = set()
            while cursor in parents and cursor not in seen:
                seen.add(cursor)
                if parents[cursor] == app_identity['pid']:
                    permitted |= descendants(cursor, parents)
                    break
                cursor = parents[cursor]
        if descendants(app_identity['pid'], parents) - {app_identity['pid']} - permitted:
            raise RuntimeError('Unaccounted-for app descendants')
    except Exception as exc:
        result['errors'].append(str(exc))
    result['clear'] = not result['errors']
    return result


class Admission:
    def __init__(self, app):
        self.app, self.lock, self.paused, self.mutations = app, threading.Lock(), False, 0

    async def __call__(self, scope, receive, send):
        mutation = scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}
        with self.lock:
            reject = self.paused and scope['type'] == 'http'
            if mutation and not reject:
                self.mutations += 1
        if reject:
            await send({'type': 'http.response.start', 'status': 503, 'headers': [(b'retry-after', b'2')]})
            await send({'type': 'http.response.body', 'body': b'Application draining'})
            return
        try:
            await self.app(scope, receive, send)
        finally:
            if mutation:
                with self.lock:
                    self.mutations -= 1


def _receive(connection, timeout=3):
    if not connection.poll(timeout):
        raise TimeoutError('Local control handshake timed out')
    return json.loads(connection.recv_bytes(32768))


def _send(connection, value):
    connection.send_bytes(json.dumps(value, sort_keys=True).encode())


def _mac(key, challenge, request):
    return hmac.new(key, challenge.encode() + json.dumps(request, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def control_loop(server, gate, config, receipt, key, event_loop, stopped):
    import _winapi
    from multiprocessing.connection import PipeConnection
    first = True
    while not stopped.is_set():
        handle = win.pipe_handle(receipt['pipe'], first=first)
        first = False
        try:
            pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
            while _winapi.WaitForSingleObject(pending.event, 100) == 258:
                if stopped.is_set():
                    pending.cancel()
                    return
            pending.GetOverlappedResult(True)
            with PipeConnection(handle) as connection:
                handle = None
                if win.user_sid(win.peer_pid(connection.fileno())) != win.user_sid():
                    raise PermissionError('Different local user')
                challenge = secrets.token_hex(32)
                _send(connection, {'challenge': challenge})
                envelope = _receive(connection)
                if not isinstance(envelope, dict) or not isinstance(envelope.get('request'), dict):
                    raise ValueError('Malformed control envelope')
                request = envelope['request']
                if not hmac.compare_digest(_mac(key, challenge, request), str(envelope.get('mac', ''))):
                    _send(connection, {'ok': False, 'error': 'authentication denied'})
                    continue
                if request.get('identity') != receipt['identity'] or request.get('action') not in {'status', 'stop'}:
                    _send(connection, {'ok': False, 'error': 'identity or action mismatch'})
                    continue
                if request['action'] == 'status':
                    _send(connection, {'ok': True, 'identity': receipt['identity'],
                                       'guard': full_guard(config, receipt['identity'], receipt['protected'])})
                    continue
                with gate.lock:
                    already = gate.paused
                    gate.paused = True
                    busy = gate.mutations != 0
                guard = full_guard(config, receipt['identity'], receipt['protected'])
                if already or busy or not guard['clear']:
                    with gate.lock:
                        if not already:
                            gate.paused = False
                    _send(connection, {'ok': False, 'state': 'blocked', 'guard': guard,
                                       'error': 'Already draining or busy/unknown workers'})
                    continue
                _send(connection, {'ok': True, 'state': 'draining'})
                def stop_app():
                    from src import shutdown
                    shutdown.request_shutdown()
                    server.should_exit = True
                event_loop.call_soon_threadsafe(stop_app)
        except (OSError, ValueError, KeyError, TypeError, TimeoutError, PermissionError):
            pass  # Malformed/unauthenticated connections never request shutdown.
        finally:
            if handle is not None:
                win.close(handle)


def request_control(directory, action, timeout=3, *, key_override=None, identity_override=None, snapshot=None):
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError('Control timeout must be finite, positive and at most 3600 seconds')
    import _winapi
    from multiprocessing.connection import PipeConnection
    directory = Path(directory)
    receipt = snapshot or json.loads((directory / 'app.json').read_text(encoding='utf-8'))
    expected = receipt['identity']
    if win.identity(expected['pid']) != expected:
        raise RuntimeError('App identity is stale or unknown')
    if not str(receipt['pipe']).startswith('\\\\.\\pipe\\odysseus-app-'):
        raise RuntimeError('Not a local app control pipe')
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Local control pipe did not become available')
        try:
            _winapi.WaitNamedPipe(receipt['pipe'], max(1, int(remaining * 1000)))
            handle = _winapi.CreateFile(receipt['pipe'], _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                                      0, _winapi.NULL, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
            break
        except OSError as exc:
            if exc.winerror not in {2, 121, 231}:
                raise
            # Retry only opening: no authentication or action has been sent.
            # A just-served pipe instance can disappear before its successor.
            if win.identity(expected['pid']) != expected:
                raise RuntimeError('App exited while opening its control pipe') from None
            time.sleep(min(.02, max(0, deadline - time.monotonic())))
    _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
    with PipeConnection(handle) as connection:
        peer = win.peer_pid(handle, server=True)
        if peer != expected['pid'] or win.identity(peer) != expected or win.user_sid(peer) != win.user_sid():
            raise PermissionError('Control server identity/user mismatch')
        challenge = _receive(connection, timeout)['challenge']
        request = {'action': action, 'identity': identity_override or expected}
        key = key_override if key_override is not None else (directory / 'secret.key').read_bytes()
        _send(connection, {'request': request, 'mac': _mac(key, challenge, request)})
        return _receive(connection, timeout)


async def serve(config):
    import uvicorn
    directory = Path(config['control_dir']).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    win.protect(directory)
    previous = directory / 'app.json'
    if previous.exists():
        old = json.loads(previous.read_text(encoding='utf-8'))
        if win.identity(old['identity']['pid']) is not None:
            raise RuntimeError('An app receipt is still live; refusing duplicate launch')
    os.environ['ODYSSEUS_DATA_DIR'] = config['state_dir']
    os.environ['APP_PORT'] = str(config['port'])
    if config.get('lab_application'):
        if config['port'] in {7000, 9130} or config['gateway_port'] in {7000, 9130} or config['chroma_port'] in {7000, 9130}:
            raise ValueError('Lab application cannot use production ports')
        os.environ['ODYSSEUS_RESTART_LAB_GATEWAY_PORT'] = str(config['gateway_port'])
        os.environ['ODYSSEUS_RESTART_LAB_DIR'] = config['state_dir']
    application = uvicorn.importer.import_from_string(config.get('lab_application') or 'app:app')
    gate = Admission(application)
    # No task-cancellation deadline: the external coordinator times out without killing.
    server = uvicorn.Server(uvicorn.Config(gate, host='127.0.0.1', port=config['port'],
                                          log_level='warning', timeout_graceful_shutdown=None))
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(.05)
    if task.done():
        await task
        raise RuntimeError('App failed to start')
    app_identity = win.identity(os.getpid())
    parents = win.process_parents()
    if config['launcher_pid'] not in _ancestors(os.getpid(), parents):
        raise RuntimeError('Launcher is not an ancestor of the app')
    receipt = {'schema_version': 1, 'identity': app_identity, 'config': config,
               'launcher': win.identity(config['launcher_pid']), 'protected': protected_state(config),
               'pipe': r'\\.\pipe\odysseus-app-' + secrets.token_hex(16)}
    key = secrets.token_hex(32).encode('ascii')
    # Replace the file object, not just its contents: an old explicit permissive
    # ACL (or an already-open old handle) must never gain access to the new key.
    atomic_write_text(str(directory / 'secret.key'), key.decode('ascii'))
    atomic_write_json(str(previous), receipt, indent=2)
    stopped = threading.Event()
    worker = threading.Thread(target=control_loop, args=(server, gate, config, receipt, key,
                              asyncio.get_running_loop(), stopped), daemon=True)
    worker.start()
    try:
        await task
    finally:
        stopped.set()
        worker.join(timeout=2)


def _ancestors(pid, parents):
    seen = set()
    while pid in parents and pid not in seen:
        seen.add(pid)
        pid = parents[pid]
    return seen


def launcher_argv(config):
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(Path(config['root']) / 'launch-windows.ps1'), '-RunOnly']
    fields = {'port': 'Port', 'python': 'Python', 'state_dir': 'StateDirectory',
              'control_dir': 'ControlDirectory', 'workers_dir': 'WorkersDirectory',
              'gateway_db': 'GatewayDatabase', 'gateway_port': 'GatewayPort', 'chroma_port': 'ChromaPort'}
    if config.get('lab_application'):
        fields['lab_application'] = 'LabApplication'
    for key, name in fields.items():
        command.extend(['-' + name, str(config[key])])
    return command


@contextmanager
def operation(directory):
    import msvcrt
    with (Path(directory) / 'operation.lock').open('a+b') as handle:
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('An app control operation is already in progress') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def restart(directory, timeout: float = 30, *, start=True):
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError('Restart timeout must be finite, positive and at most 3600 seconds')
    with operation(directory):
        return _restart(directory, timeout, start=start)


def _restart(directory, timeout, *, start):
    receipt = json.loads((Path(directory) / 'app.json').read_text(encoding='utf-8'))
    response = request_control(directory, 'stop', snapshot=receipt)
    if not response.get('ok'):
        return response
    deadline = time.monotonic() + timeout
    while any(win.identity(value['pid']) == value for value in (receipt['identity'], receipt['launcher'])):
        if time.monotonic() >= deadline:
            return {'ok': False, 'state': 'shutdown-timeout', 'forced': False, 'replacement_started': False}
        time.sleep(.05)
    try:
        verify_protected(receipt['protected'])
    except Exception as exc:
        return {'ok': False, 'state': 'protected-service-changed', 'error': str(exc),
                'process_stopped': True, 'replacement_started': False, 'forced': False}
    if not start:
        return {'ok': True, 'state': 'stopped', 'protected_unchanged': True}
    config = receipt['config']
    log = Path(directory) / 'launcher.log'
    with log.open('ab') as stream:
        child = subprocess.Popen(launcher_argv(config), stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = json.loads((Path(directory) / 'app.json').read_text(encoding='utf-8'))
            value = current['identity']
            if value != receipt['identity'] and win.identity(value['pid']) == value and win.listener_pid(config['port']) == value['pid'] and health(config['port']):
                observed = request_control(directory, 'status')
                if observed.get('ok') and observed['identity'] == value:
                    verify_protected(receipt['protected'])
                    return {'ok': True, 'state': 'healthy', 'identity': value,
                            'previous_identity': receipt['identity'], 'protected_unchanged': True}
        except (OSError, ValueError, RuntimeError, KeyError):
            pass
        time.sleep(.1)
    return {'ok': False, 'state': 'startup-timeout', 'forced': False,
            'replacement_started': True, 'launcher_pid': child.pid}
