"""New real stdio/Windows pipe drain coverage, not a repeat of earlier rehearsals."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_windows_app_restart_integration import lab, wait_for

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Windows process ownership')


def fetch(config, method, path) -> tuple[int, Any]:
    connection = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=25)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        return response.status, (json.loads(body) if response.getheader('Content-Type', '').startswith('application/json') else body.decode())
    finally:
        connection.close()


def test_real_stdio_call_drains_before_app_exit_and_preserves_services(lab):
    from src import windows_restart_os as win
    from src.windows_app_restart import request_control, restart, verify_protected
    config, old, _, root = lab
    status, started = fetch(config, 'POST', '/fixture-mcp-start')
    assert status == 200 and started['connected'] is True
    peer_dir = Path(config['state_dir']) / 'mcp-peer'
    peer = win.identity(int((peer_dir / 'peer.pid').read_text()))
    assert peer is not None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            running = executor.submit(fetch, config, 'POST', '/fixture-mcp-call')
            wait_for(lambda: (peer_dir / 'entered').exists())
            guard = request_control(config['control_dir'], 'status')['guard']
            assert guard['mcp']['in_flight'] == 1
            assert guard['mcp']['unknown'] == 0
            assert not guard['clear']
            stopping = executor.submit(restart, config['control_dir'], timeout=12, start=False)
            wait_for(lambda: fetch(config, 'GET', '/api/health')[0] == 503)
            assert not running.done() and not stopping.done()
            (peer_dir / 'release').touch()
            status, completed = running.result(timeout=10)
            assert status == 200 and 'fixture-completed' in completed['stdout']
            result = stopping.result(timeout=15)
            assert result['ok'] is True and result['state'] == 'stopped'
        assert win.identity(peer['pid']) != peer
        verify_protected(old['protected'])
    finally:
        (peer_dir / 'release').touch()


def test_real_stdio_cleanup_timeout_does_not_force_exit(lab):
    from src import windows_restart_os as win
    from src.windows_app_restart import request_control, restart, verify_protected
    config, old, _, root = lab
    peer_dir = Path(config['state_dir']) / 'mcp-peer'
    peer_dir.mkdir()
    hold = peer_dir / 'hold-exit'
    hold.touch()
    try:
        assert fetch(config, 'POST', '/fixture-mcp-start')[1]['connected'] is True
        peer = win.identity(int((peer_dir / 'peer.pid').read_text()))
        assert peer is not None
        result = restart(config['control_dir'], timeout=8, start=False)
        assert not result['ok'] and result['state'] == 'mcp-close-incomplete'
        assert win.identity(peer['pid']) == peer, 'SDK cleanup must not kill the stubborn owned peer'
        assert win.identity(old['identity']['pid']) == old['identity']
        guard = request_control(config['control_dir'], 'status')['guard']
        assert guard['mcp']['unknown'] > 0 and not guard['clear']
        assert 'stdio_cleanup_timeout' in guard['mcp']['unknown_reasons']
        verify_protected(old['protected'])
    finally:
        hold.unlink(missing_ok=True)
    wait_for(lambda: win.identity(peer['pid']) != peer)
