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
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request

from core.atomic_io import atomic_write_json, atomic_write_text, store_transaction
from src import windows_restart_os as win

TERMINAL = {'done', 'completed', 'finished', 'error', 'failed', 'stopped', 'cancelled', 'canceled', 'ended', 'killed'}


def observe_worker_receipt(file):
    """Observe the recorded incarnation; never retire files, tasks or a live PID."""
    observation = {'session_id': file.stem, 'state': 'unknown', 'signal_authorized': False}
    try:
        raw = file.read_bytes()
        pid = int(raw.decode('utf-8').strip())
        if pid <= 0:
            return observation
        token_file = file.with_suffix('.pid.token')
        raw_token = token_file.read_bytes() if token_file.is_file() else None
        token = raw_token.decode('utf-8').strip() if raw_token is not None else None
        current = win.identity(pid)
        if current is None:
            state = 'gone'
        elif token and re.fullmatch(r'win32:[1-9][0-9]*', token):
            state = 'live' if current['start_token'] == token else 'obsolete'
        else:
            state = 'unknown'
        if (file.read_bytes() != raw or win.identity(pid) != current
                or (token_file.read_bytes() if token_file.is_file() else None) != raw_token):
            state = 'unknown'
        observation.update(pid=pid, state=state)
    except (OSError, ValueError):
        pass
    return observation


def _native_background_processes():
    """No commands/credentials leave the OS query; incomplete visibility is unknown."""
    script = '''$ErrorActionPreference='Stop';
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();
$p=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'");
@{complete=(@($p | Where-Object {[string]::IsNullOrWhiteSpace($_.CommandLine)}).Count -eq 0 -and ($p.ProcessId -contains SELF_PID));
supervisors=@($p | Where-Object {$_.CommandLine -match '(containment_worker|bg_worker)\\.py'} |
 ForEach-Object {[int]$_.ProcessId})} | ConvertTo-Json -Compress'''
    try:
        raw = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            script.replace('SELF_PID', str(os.getpid()))], timeout=5,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'complete': False, 'supervisors': None}


def _uninitialized_background(state_dir):
    from src.native_workers import registry_for
    directory = Path(state_dir)
    def no_artifacts():
        if not directory.is_dir() or any((directory / name).exists() or (directory / name).is_symlink()
                                         for name in ('bg_jobs.json', 'bg_jobs', 'containment_grants.json')):
            return False
        publications = directory / 'process_resources'
        if publications.is_symlink():
            return False
        if not publications.exists():
            return True
        if not publications.is_dir():
            return False
        for file in publications.iterdir():
            if file.name == 'publication.lock' and file.is_file() and not file.is_symlink():
                continue
            if file.name == 'native_workers' and file.is_dir() and not file.is_symlink():
                snapshot = registry_for(str(publications)).snapshot()
                if snapshot['initialized'] and not snapshot['unknown'] and not any(
                        row['source'] == 'background' for row in snapshot['workers']):
                    continue
            return False
        return True
    try:
        if not no_artifacts():
            return False
        snapshot = _native_background_processes()
        return (isinstance(snapshot, dict) and snapshot.get('complete') is True
                and snapshot.get('supervisors') == [] and no_artifacts())
    except OSError:
        return False


_BG_JOB_ARTIFACT_SUFFIXES = frozenset({'log', 'exit', 'result.json', 'authority.json', 'sh', 'cmd.sh'})


def _bg_jobs_recoverable(state_dir):
    """Read-only idle proof for a mature install whose bg_jobs.json is absent.

    Unlike _uninitialized_background (which requires zero launch artifacts),
    this accounts for the artifacts a long-lived install legitimately keeps.
    ALL of: a complete supervisor scan with zero supervisors; an initialized
    native registry with every worker terminal; no unreleased background
    grant naming a live or unverifiable supervisor; and every bg_jobs/
    remnant job carrying a durable exit code — re-checked against artifacts
    appearing mid-proof. Any ambiguity is not recoverable; the guard keeps
    refusing. Never writes; materialization belongs to serve().
    """
    from src import process_ownership
    from src.native_workers import registry_for
    directory = Path(state_dir)
    store = directory / 'bg_jobs.json'

    def accounted():
        if store.exists() or store.is_symlink():
            return False
        jobs_dir = directory / 'bg_jobs'
        if jobs_dir.is_symlink():
            return False
        if jobs_dir.exists():
            if not jobs_dir.is_dir():
                return False
            seen, exited = set(), set()
            for file in jobs_dir.iterdir():
                if file.is_symlink() or not file.is_file():
                    return False
                job_id, dot, suffix = file.name.partition('.')
                if not dot or not re.fullmatch(r'[0-9a-f]{12}', job_id) or suffix not in _BG_JOB_ARTIFACT_SUFFIXES:
                    return False
                seen.add(job_id)
                if suffix == 'exit':
                    try:
                        int(file.read_text(encoding='utf-8').strip() or '1')
                    except (OSError, ValueError):
                        return False
                    exited.add(job_id)
            if seen - exited:
                return False
        grants_file = directory / 'containment_grants.json'
        if grants_file.is_symlink():
            return False
        if grants_file.exists():
            if not grants_file.is_file():
                return False
            try:
                grants = json.loads(grants_file.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return False
            if not isinstance(grants, dict):
                return False
            for record in grants.values():
                if not isinstance(record, dict):
                    return False
                if record.get('released_at') or record.get('lifetime') != 'background':
                    continue
                pid, token = record.get('supervisor_pid'), record.get('supervisor_token')
                if type(pid) is not int or pid <= 0 or not token:
                    return False
                if process_ownership.verify(pid, token) not in {process_ownership.GONE, process_ownership.FOREIGN}:
                    return False
        return True

    try:
        if not accounted():
            return False
        snapshot = registry_for(str(directory / 'process_resources')).snapshot()
        if not snapshot['initialized'] or snapshot['unknown'] or any(
                row['state'] not in {'completed', 'failed', 'cancelled'} for row in snapshot['workers']):
            return False
        scan = _native_background_processes()
        if not (isinstance(scan, dict) and scan.get('complete') is True and scan.get('supervisors') == []):
            return False
        return accounted()
    except Exception:
        return False


def prepare_bg_jobs_inventory(config):
    """Materialize an empty bg_jobs inventory at serve() startup, only under proof.

    A mature install whose store was never written (or was lost) otherwise
    refuses cooperative restart forever. The empty dict is exactly the schema
    bg_jobs and file_guard accept, written atomically under the same store
    lock as every bg_jobs writer, and only while _bg_jobs_recoverable proves
    no live background work — before and after taking the lock. An existing
    store is never touched; any ambiguity leaves it absent so the guard keeps
    refusing. Recovery is best-effort: it never blocks or fails startup.
    """
    store = Path(config['state_dir']) / 'bg_jobs.json'
    if store.is_symlink():
        return {'state': 'unproven', 'initialized': False}
    if store.exists():
        return {'state': 'present', 'initialized': False}
    if not _bg_jobs_recoverable(config['state_dir']):
        return {'state': 'unproven', 'initialized': False}

    @store_transaction(lambda: store)
    def materialize():
        if store.is_symlink():
            return {'state': 'unproven', 'initialized': False}
        if store.exists():
            return {'state': 'present', 'initialized': False}
        if not _bg_jobs_recoverable(config['state_dir']):
            return {'state': 'unproven', 'initialized': False}
        atomic_write_json(str(store), {})
        return {'state': 'initialized', 'initialized': True}

    try:
        return materialize()
    except OSError:
        return {'state': 'unproven', 'initialized': False}


def file_guard(state_dir, workers_dir, gateway_db):
    errors = []
    receipts = []
    background = {'state': 'unknown'}
    native_workers = None
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
            elif filename == 'bg_jobs.json':
                background = {'state': 'recorded_idle'}
        except FileNotFoundError:
            if filename == 'bg_jobs.json' and _uninitialized_background(state_dir):
                background = {'state': 'uninitialized_idle', 'basis': 'no launch artifacts and complete supervisor scan'}
            elif filename == 'bg_jobs.json' and _bg_jobs_recoverable(state_dir):
                background = {'state': 'recovered_idle',
                              'basis': 'complete supervisor scan, terminal native registry and accounted launch artifacts'}
            else:
                errors.append('unknown ' + filename)
        except Exception:
            errors.append('unknown ' + filename)
    try:
        directory = Path(workers_dir)
        if not directory.is_dir():
            raise ValueError('missing worker inventory')
        for file in directory.glob('*.pid'):
            observation = observe_worker_receipt(file)
            receipts.append(observation)
            if observation['state'] not in {'gone', 'obsolete'}:
                errors.append('live or unknown worker receipt')
    except Exception:
        errors.append('unknown worker inventory')
    from src.native_workers import registry_for
    native = registry_for(str(Path(state_dir) / 'process_resources'))
    if native.path.parent.exists():
        native_workers = native.snapshot()
        if native_workers['unknown'] or any(row['state'] not in {'completed', 'failed', 'cancelled'}
                                           for row in native_workers['workers']):
            errors.append('live or unknown durable worker inventory')
    return {'clear': not errors, 'errors': errors, 'worker_receipts': receipts,
            'background_jobs': background, 'native_workers': native_workers}


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


def full_guard(config, app_identity, protected, *, allow_mcp_busy=False):
    result = file_guard(config['state_dir'], config['workers_dir'], config['gateway_db'])
    try:
        if win.identity(app_identity['pid']) != app_identity or win.listener_pid(config['port']) != app_identity['pid']:
            raise RuntimeError('App identity/listener mismatch')
        verify_protected(protected)
        parents = win.process_parents()
        permitted = set()
        from src.native_mcp_activity import REGISTRY
        from src.native_mcp_stdio import validate
        validate(REGISTRY)
        permitted |= REGISTRY.accounted_process_ids()
        result['mcp'] = REGISTRY.snapshot()
        if not result['mcp']['initialized'] or result['mcp']['unknown']:
            result['errors'].append('Native MCP activity is unknown or untracked')
        if result['mcp']['in_flight'] and not allow_mcp_busy:
            result['errors'].append('Native MCP operations are active')
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
        self.preparing = False

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


def _commit_shutdown(server):
    from src import shutdown
    shutdown.request_shutdown()
    server.should_exit = True


async def prepare_shutdown(server, gate, config, identity, protected, timeout: float = 30, *, deadline: float | None = None):
    with gate.lock:
        if gate.preparing:
            return {'ok': False, 'state': 'already-draining', 'forced': False}
        gate.preparing = True
    try:
        return await _prepare_shutdown(server, gate, config, identity, protected, timeout, deadline)
    finally:
        with gate.lock:
            gate.preparing = False


async def _prepare_shutdown(server, gate, config, identity, protected, timeout: float, deadline: float | None):
    from src.native_workers import registry_for, WorkerStateUnknown, AdmissionClosed
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('MCP shutdown observation must be finite, positive and at most 300 seconds')
    deadline = min(deadline, time.monotonic() + timeout) if deadline is not None else time.monotonic() + timeout
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        return {'ok': False, 'state': 'control-timeout', 'forced': False}
    if not config.get('state_dir'):
        return {'ok': False, 'state': 'worker-inventory-unknown', 'forced': False}
    workers = registry_for(str(Path(config['state_dir']) / 'process_resources'))
    try:
        token = await asyncio.to_thread(workers.fence)
    except (WorkerStateUnknown, AdmissionClosed, OSError):
        return {'ok': False, 'state': 'worker-admission-unknown', 'forced': False}
    with gate.lock:
        previous_pause, gate.paused = gate.paused, True
    try:
        snapshot = workers.snapshot()
        if snapshot['unknown'] or any(row['state'] not in {'completed', 'failed', 'cancelled'}
                                     for row in snapshot['workers']):
            return {'ok': False, 'state': 'native-workers-busy-or-unknown', 'workers': snapshot, 'forced': False}
        return await _prepare_mcp_shutdown(server, gate, config, identity, protected, timeout, deadline)
    finally:
        if not server.should_exit:
            with gate.lock:
                gate.paused = previous_pause
            await asyncio.to_thread(workers.resume, token)


async def _prepare_mcp_shutdown(server, gate, config, identity, protected, timeout: float, deadline: float | None):
    """Fence, observe accepted work, close owned peers without force, then commit."""
    from src.native_mcp_activity import REGISTRY, AdmissionClosed
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('MCP shutdown observation must be finite, positive and at most 300 seconds')
    deadline = min(deadline, time.monotonic() + timeout) if deadline is not None else time.monotonic() + timeout
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        return {'ok': False, 'state': 'control-timeout', 'forced': False}
    guard = await asyncio.to_thread(full_guard, config, identity, protected, allow_mcp_busy=True)
    if not guard['clear'] or time.monotonic() >= deadline:
        return {'ok': False, 'state': 'blocked', 'guard': guard, 'forced': False}
    try:
        generation = REGISTRY.fence()
    except AdmissionClosed:
        return {'ok': False, 'state': 'already-draining', 'mcp': REGISTRY.snapshot(), 'forced': False}
    with gate.lock:
        gate.paused = True
    closing = REGISTRY.connections_closed
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {'ok': False, 'state': 'mcp-drain-timeout', 'forced': False}
        result = await REGISTRY.wait_idle(remaining)
        if not result['ok']:
            return {**result, 'forced': False}
        while True:
            with gate.lock:
                busy = gate.mutations != 0
            if not busy:
                break
            if time.monotonic() >= deadline:
                return {'ok': False, 'state': 'app-drain-timeout', 'forced': False}
            await asyncio.sleep(.02)
        guard = await asyncio.to_thread(full_guard, config, identity, protected)
        if not guard['clear'] or time.monotonic() >= deadline:
            return {'ok': False, 'state': 'blocked', 'guard': guard, 'forced': False}
        closing = True
        if not await REGISTRY.close_connections(max(.001, deadline - time.monotonic())):
            return {'ok': False, 'state': 'mcp-close-incomplete', 'mcp': REGISTRY.snapshot(), 'forced': False}
        guard = await asyncio.to_thread(full_guard, config, identity, protected)
        if not guard['clear'] or time.monotonic() >= deadline:
            return {'ok': False, 'state': 'blocked-after-mcp-close', 'guard': guard, 'forced': False}
        with REGISTRY.lock:
            state = REGISTRY.snapshot()
            if time.monotonic() >= deadline:
                return {'ok': False, 'state': 'control-timeout', 'forced': False}
            if state['unknown'] or state['in_flight'] or not state['connections_closed']:
                return {'ok': False, 'state': 'mcp-activity-unknown', 'mcp': state, 'forced': False}
            _commit_shutdown(server)
        return {'ok': True, 'state': 'draining', 'mcp': REGISTRY.snapshot(), 'forced': False}
    finally:
        if not server.should_exit:
            with gate.lock:
                gate.paused = False
            if not closing and not REGISTRY.snapshot()['unknown']:
                REGISTRY.resume(generation)


def _receive(connection, timeout: float = 3):
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
                deadline = request.get('deadline', time.monotonic() + 30)
                if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
                    raise ValueError('Invalid shutdown deadline')
                budget = min(300, deadline - time.monotonic())
                if budget <= 0:
                    _send(connection, {'ok': False, 'state': 'control-timeout', 'forced': False})
                    continue
                future = asyncio.run_coroutine_threadsafe(prepare_shutdown(
                    server, gate, config, receipt['identity'], receipt['protected'], budget, deadline=deadline), event_loop)
                _send(connection, future.result(timeout=budget + .05))
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
        challenge = _receive(connection, max(0, deadline - time.monotonic()))['challenge']
        request = {'action': action, 'identity': identity_override or expected, 'deadline': deadline}
        key = key_override if key_override is not None else (directory / 'secret.key').read_bytes()
        _send(connection, {'request': request, 'mac': _mac(key, challenge, request)})
        return _receive(connection, max(0, deadline - time.monotonic()))


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
    from src.native_workers import registry_for
    await asyncio.to_thread(registry_for(str(Path(config['state_dir']) / 'process_resources')).initialize)
    await asyncio.to_thread(prepare_bg_jobs_inventory, config)
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
    deadline = time.monotonic() + timeout
    try:
        response = request_control(directory, 'stop', timeout=timeout, snapshot=receipt)
    except TimeoutError:
        return {'ok': False, 'state': 'control-timeout', 'stop_outcome': 'unconfirmed',
                'forced': False, 'replacement_started': False}
    if not response.get('ok'):
        return response
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
