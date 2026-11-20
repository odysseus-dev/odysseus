"""Focused rebaseline guards: authenticated refresh, fail-closed refusals, tamper detection.

Unit lens: real local HTTP health contracts + real named-pipe control channel,
with deterministic fake process identities/listeners so a shared-service
"restart" (pid incarnation change) can be rehearsed without killing anything.
Real-process end-to-end recovery lives in test_windows_app_restart_integration.
"""
import asyncio
from contextlib import closing, contextmanager, ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import types

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Native Windows control infrastructure')


@contextmanager
def endpoint(body):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class World:
    def __init__(self, tmp_path, monkeypatch, gateway_body, chroma_body):
        from src import windows_app_restart as control
        from src import windows_restart_os as win
        self.control = control
        self.win = win
        self.identities = {}
        self.listeners = {}
        monkeypatch.setattr(win, 'identity', lambda pid: self.identities.get(int(pid)))
        def listener(port):
            if port not in self.listeners:
                raise RuntimeError(f'Expected exactly one IPv4 listener on {port}')
            return self.listeners[port]
        monkeypatch.setattr(win, 'listener_pid', listener)
        self.stack = ExitStack()
        self.gateway_server = self.stack.enter_context(endpoint(
            {'ok': True, 'auth_required': False, 'displayVersion': 'fixture'}
            if gateway_body is None else gateway_body))
        self.chroma_server = self.stack.enter_context(endpoint(
            {'nanosecond heartbeat': 1} if chroma_body is None else chroma_body))
        self.gateway_port = self.gateway_server.server_port
        self.chroma_port = self.chroma_server.server_port
        self.directory = tmp_path / 'control'
        self.directory.mkdir()
        self.key = secrets.token_hex(32).encode('ascii')
        (self.directory / 'secret.key').write_bytes(self.key)
        self.app_port = 41000  # fake listener; rebaseline never binds the app port
        self.config = {'port': self.app_port, 'gateway_port': self.gateway_port,
                       'chroma_port': self.chroma_port, 'control_dir': str(self.directory)}
        self.app_identity = {'pid': os.getpid(), 'start_token': 'win32:1000'}
        self.launcher = {'pid': 4242, 'start_token': 'win32:4242'}
        self.identities[os.getpid()] = self.app_identity
        self.identities[4242] = self.launcher
        self.listeners[self.app_port] = os.getpid()
        self.gateway_pid = 901
        self.chroma_pid = 902
        self.set_service('gateway', self.gateway_pid)
        self.set_service('chroma', self.chroma_pid)
        self.receipt = {'schema_version': 1, 'identity': self.app_identity, 'config': self.config,
                        'launcher': self.launcher, 'protected': self.fabricated_baseline(),
                        'pipe': r'\\.\pipe\odysseus-app-' + secrets.token_hex(16)}
        self.write_receipt()
        self.thread = None

    def set_service(self, kind, pid):
        self.identities[pid] = {'pid': pid, 'start_token': f'win32:{pid}'}
        port = self.gateway_port if kind == 'gateway' else self.chroma_port
        self.listeners[port] = pid

    def fabricated_baseline(self):
        # As captured at launch while both services were healthy.
        return [{'port': self.gateway_port, 'path': '/api/health', 'kind': 'gateway',
                 'pid': self.gateway_pid, 'start_token': f'win32:{self.gateway_pid}'},
                {'port': self.chroma_port, 'path': '/api/v2/heartbeat', 'kind': 'chroma',
                 'pid': self.chroma_pid, 'start_token': f'win32:{self.chroma_pid}'}]

    def write_receipt(self):
        (self.directory / 'app.json').write_text(json.dumps(self.receipt, indent=2), encoding='utf-8')
    def disk(self):
        return json.loads((self.directory / 'app.json').read_text(encoding='utf-8'))
    def bytes(self):
        return (self.directory / 'app.json').read_bytes()
    def log(self):
        return [json.loads(line) for line in
                (self.directory / 'rebaseline.log').read_text(encoding='utf-8').splitlines()]

    def stop_gateway(self):
        self.gateway_server.shutdown()
        self.gateway_server.server_close()

    def start_control(self):
        self.server = types.SimpleNamespace(should_exit=False)
        self.gate = self.control.Admission(None)
        self.stopped = threading.Event()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.control.control_loop,
                                       args=(self.server, self.gate, self.config, self.receipt,
                                             self.key, self.loop, self.stopped), daemon=True)
        self.thread.start()

    def close(self):
        if self.thread is not None:
            self.stopped.set()
            self.thread.join(timeout=3)
            self.loop.close()
        self.stack.close()


@pytest.fixture
def world_factory(tmp_path, monkeypatch):
    worlds = []
    def make(gateway_body=None, chroma_body=None):
        world = World(tmp_path, monkeypatch, gateway_body, chroma_body)
        worlds.append(world)
        return world
    yield make
    for world in worlds:
        world.close()


def raw_request(world, request, key=None):
    """Manual pipe handshake without the client-side checks of request_control,
    so server-side authentication/identity refusals can be observed directly."""
    import _winapi
    from multiprocessing.connection import PipeConnection
    receipt = json.loads((world.directory / 'app.json').read_text(encoding='utf-8'))
    deadline = time.monotonic() + 5
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('control pipe did not become available')
        try:
            _winapi.WaitNamedPipe(receipt['pipe'], max(1, int(remaining * 1000)))
            handle = _winapi.CreateFile(receipt['pipe'], _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                                        0, _winapi.NULL, _winapi.OPEN_EXISTING,
                                        _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
            break
        except OSError as exc:
            if exc.winerror not in {2, 121, 231}:
                raise
            time.sleep(.02)
    _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
    with PipeConnection(handle) as connection:
        challenge = json.loads(connection.recv_bytes(32768))['challenge']
        mac = world.control._mac(world.key if key is None else key, challenge, request)
        connection.send_bytes(json.dumps({'request': request, 'mac': mac}, sort_keys=True).encode())
        return json.loads(connection.recv_bytes(32768))


def test_rebaseline_refreshes_only_protected_after_legitimate_restart(world_factory):
    world = world_factory()
    control = world.control
    original = json.loads(world.bytes())
    control.verify_protected(world.receipt['protected'])  # baseline valid at "launch"
    # Legitimate gateway restart: old incarnation gone, fresh pid listens.
    del world.identities[world.gateway_pid]
    world.set_service('gateway', 903)
    with pytest.raises(RuntimeError, match='Protected service identity or health changed'):
        control.verify_protected(world.receipt['protected'])
    world.start_control()
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is True, result
    assert result['state'] == 'rebaselined'
    assert result['forced'] is False
    assert result['protected'][0]['pid'] == 903
    assert result['protected'][1] == original['protected'][1]  # untouched service kept
    assert result['previous_protected'] == original['protected']
    disk = world.disk()
    assert disk['protected'] == result['protected']
    # ONLY the protected field changed on disk.
    disk_other = {k: v for k, v in disk.items() if k != 'protected'}
    original_other = {k: v for k, v in original.items() if k != 'protected'}
    assert disk_other == original_other
    # The live control state adopted the fresh baseline for later stop/status.
    control.verify_protected(world.receipt['protected'])
    again = control.rebaseline(world.directory, timeout=5)
    assert again['ok'] is True and again['protected'] == result['protected']
    log = world.log()
    assert len(log) == 2 and all(row['ok'] is True for row in log)
    assert log[0]['previous_protected'][0]['pid'] == world.gateway_pid
    assert log[0]['protected'][0]['pid'] == 903
    assert log[0]['requester_pid'] == os.getpid()


@pytest.mark.parametrize('bad', ['gateway-contract', 'chroma-contract', 'gateway-down'])
def test_rebaseline_refuses_unhealthy_service(world_factory, bad):
    if bad == 'gateway-contract':
        world = world_factory(gateway_body={'status': 'healthy'})
    elif bad == 'chroma-contract':
        world = world_factory(chroma_body={'ok': True})
    else:
        world = world_factory()
        world.stop_gateway()
    control = world.control
    before = world.bytes()
    world.start_control()
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is False, result
    assert result['state'] == 'rebaseline-refused'
    assert result['error'] == 'Shared service is not healthy'
    assert world.bytes() == before
    assert world.receipt['protected'] == json.loads(before)['protected']
    assert world.log()[-1]['ok'] is False


def test_rebaseline_refuses_unavailable_listener_identity(world_factory):
    world = world_factory()
    control = world.control
    # Fresh incarnation listens and answers the health contract, but its
    # process identity cannot be captured: rebaseline must stay fail-closed.
    del world.identities[world.gateway_pid]
    world.set_service('gateway', 903)
    del world.identities[903]
    before = world.bytes()
    world.start_control()
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is False, result
    assert result['error'] == 'Shared listener identity unavailable'
    assert world.bytes() == before
    assert world.log()[-1]['ok'] is False


def test_rebaseline_refuses_wrong_local_user(world_factory, monkeypatch):
    world = world_factory()
    real_sid = world.win.user_sid()
    # Peer processes present a different SID; the server's own SID (no-arg
    # calls, used for pipe ACLs) stays real so the pipe remains connectable.
    monkeypatch.setattr(world.win, 'user_sid',
                        lambda pid=None: 'S-1-5-21-1-2-3-999' if pid is not None else real_sid)
    before = world.bytes()
    world.start_control()
    request = {'action': 'rebaseline', 'identity': world.receipt['identity']}
    with pytest.raises((EOFError, OSError)):
        raw_request(world, request)  # connection dropped before any challenge/response
    assert world.bytes() == before
    assert not (world.directory / 'rebaseline.log').exists()  # handler never reached


def test_rebaseline_refuses_bad_mac(world_factory):
    world = world_factory()
    before = world.bytes()
    world.start_control()
    request = {'action': 'rebaseline', 'identity': world.receipt['identity']}
    result = raw_request(world, request, key=b'not-the-secret')
    assert result == {'ok': False, 'error': 'authentication denied'}
    assert world.bytes() == before
    assert not (world.directory / 'rebaseline.log').exists()


def test_rebaseline_refuses_wrong_identity(world_factory):
    world = world_factory()
    control = world.control
    before = world.bytes()
    world.start_control()
    result = control.request_control(world.directory, 'rebaseline', timeout=5,
                                     identity_override={'pid': 1, 'start_token': 'win32:1'})
    assert result == {'ok': False, 'error': 'identity or action mismatch'}
    assert world.bytes() == before


@pytest.mark.parametrize('action', ['force-rebaseline', 'restart', 'rebaseline-force'])
def test_unknown_actions_still_refused(world_factory, action):
    world = world_factory()
    before = world.bytes()
    world.start_control()
    request = {'action': action, 'identity': world.receipt['identity']}
    result = raw_request(world, request)
    assert result == {'ok': False, 'error': 'identity or action mismatch'}
    assert world.bytes() == before


def test_rebaseline_refuses_stale_app_identity_before_pipe(world_factory):
    world = world_factory()
    control = world.control
    # The receipt's app incarnation no longer matches the live process: the
    # client must refuse before any pipe IO (same guard as every action).
    world.identities[os.getpid()] = {'pid': os.getpid(), 'start_token': 'win32:999999'}
    before = world.bytes()
    with pytest.raises(RuntimeError, match='stale or unknown'):
        control.rebaseline(world.directory, timeout=5)
    assert world.bytes() == before


def test_rebaseline_refuses_stale_app_listener(world_factory):
    world = world_factory()
    control = world.control
    # Someone else now owns the app port: the app is no longer the live listener.
    world.listeners[world.app_port] = 4242
    before = world.bytes()
    world.start_control()
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is False, result
    assert result['error'] == 'App identity/listener mismatch'
    assert world.bytes() == before
    assert world.log()[-1]['ok'] is False


def test_rebaseline_refuses_stale_app_identity_server_side(world_factory):
    world = world_factory()
    control = world.control
    world.identities[os.getpid()] = {'pid': os.getpid(), 'start_token': 'win32:999999'}
    before = world.bytes()
    result = control.rebaseline_protected(world.config, world.receipt)
    assert result['ok'] is False, result
    assert result['error'] == 'App identity/listener mismatch'
    assert world.bytes() == before
    assert world.log()[-1]['ok'] is False


@pytest.mark.parametrize('field', ['protected', 'config', 'launcher'])
def test_rebaseline_refuses_tampered_receipt(world_factory, field):
    world = world_factory()
    control = world.control
    original = json.loads(world.bytes())
    tampered = json.loads(world.bytes())
    if field == 'protected':
        # Forge a baseline that points at the OTHER live healthy service.
        tampered['protected'][0] = {**tampered['protected'][1], 'port': world.gateway_port,
                                    'path': '/api/health', 'kind': 'gateway'}
    elif field == 'config':
        tampered['config']['port'] = world.app_port + 1
    else:
        tampered['launcher']['pid'] = 4243
    (world.directory / 'app.json').write_text(json.dumps(tampered, indent=2), encoding='utf-8')
    tampered_bytes = world.bytes()
    world.start_control()
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is False, result
    assert result['error'] == 'App receipt does not match the live control state'
    assert world.bytes() == tampered_bytes  # refusal never rewrites the receipt
    assert world.receipt['protected'] == original['protected']  # memory untouched
    assert world.log()[-1]['ok'] is False


def test_rebaseline_refuses_unreadable_receipt(world_factory):
    world = world_factory()
    control = world.control
    (world.directory / 'app.json').unlink()
    result = control.rebaseline_protected(world.config, world.receipt)
    assert result['ok'] is False, result
    assert result['error'] == 'App receipt is unreadable'
    assert world.log()[-1]['ok'] is False


@pytest.mark.parametrize('mode', ['server-exit', 'gate-preparing'])
def test_rebaseline_refuses_while_draining(world_factory, mode):
    world = world_factory()
    control = world.control
    before = world.bytes()
    world.start_control()
    if mode == 'server-exit':
        world.server.should_exit = True
    else:
        world.gate.preparing = True
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is False, result
    assert result['error'] == 'Application is draining'
    assert world.bytes() == before
    assert world.log()[-1]['ok'] is False


def test_rebaseline_refuses_during_other_control_operation(world_factory):
    world = world_factory()
    control = world.control
    before = world.bytes()
    world.start_control()
    with control.operation(world.directory):
        result = control.rebaseline(world.directory, timeout=5)
        assert result['ok'] is False, result
        assert 'in progress' in result['error']
    assert world.bytes() == before
    assert world.log()[-1]['ok'] is False


def test_status_uses_refreshed_baseline_after_rebaseline(world_factory, tmp_path):
    world = world_factory()
    control = world.control
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'cookbook_state.json').write_text('{"tasks": []}', encoding='utf-8')
    (state / 'bg_jobs.json').write_text('{}', encoding='utf-8')
    workers = tmp_path / 'workers'
    workers.mkdir()
    db = tmp_path / 'gateway.db'
    with closing(sqlite3.connect(db)) as con:
        con.execute('create table session_turn_leases(expires_at real)')
    world.config.update(state_dir=str(state), workers_dir=str(workers), gateway_db=str(db))
    world.write_receipt()
    del world.identities[world.gateway_pid]
    world.set_service('gateway', 903)
    world.start_control()
    before = control.request_control(world.directory, 'status', timeout=5)
    assert before['ok'] is True
    assert 'Protected service identity or health changed' in before['guard']['errors']
    result = control.rebaseline(world.directory, timeout=5)
    assert result['ok'] is True, result
    after = control.request_control(world.directory, 'status', timeout=5)
    assert after['ok'] is True
    assert 'Protected service identity or health changed' not in after['guard']['errors']
