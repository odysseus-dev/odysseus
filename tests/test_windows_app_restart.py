"""Focused fail-closed guards; integration uses real Windows processes elsewhere."""
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Native Windows control infrastructure')


def files(tmp_path):
    state = tmp_path / 'data'
    state.mkdir()
    (state / 'cookbook_state.json').write_text('{"tasks": []}', encoding='utf-8')
    (state / 'bg_jobs.json').write_text('{}', encoding='utf-8')
    logs = tmp_path / 'workers'
    logs.mkdir()
    db = tmp_path / 'gateway.db'
    with sqlite3.connect(db) as con:
        con.execute('create table session_turn_leases(expires_at real)')
    return state, logs, db


def test_guard_missing_evidence_blocks(tmp_path):
    from src.windows_app_restart import file_guard
    result = file_guard(tmp_path, tmp_path / 'workers', tmp_path / 'missing.db')
    assert result['clear'] is False
    assert result['errors']


def test_guard_idle_and_busy(tmp_path):
    from src.windows_app_restart import file_guard
    state, logs, db = files(tmp_path)
    assert file_guard(state, logs, db)['clear'] is True
    with sqlite3.connect(db) as con:
        con.execute('insert into session_turn_leases values (?)', (time.time() + 60,))
    assert file_guard(state, logs, db)['clear'] is False
    with sqlite3.connect(db) as con:
        con.execute('delete from session_turn_leases')
    (state / 'cookbook_state.json').write_text(json.dumps({'tasks': [{'status': 'running', 'type': 'download'}]}))
    assert file_guard(state, logs, db)['clear'] is False


def test_guard_unknown_worker_file_blocks(tmp_path):
    from src.windows_app_restart import file_guard
    state, logs, db = files(tmp_path)
    (logs / 'unknown.pid').write_text('not-a-pid')
    assert file_guard(state, logs, db)['clear'] is False


def test_concurrent_restart_operation_is_blocked_before_contacting_app(tmp_path):
    from src.windows_app_restart import operation, restart
    with operation(tmp_path):
        with pytest.raises(RuntimeError, match='in progress'):
            restart(tmp_path)


def test_control_directory_acl_is_protected_and_owner_system_only(tmp_path):
    import os
    import subprocess
    from src import windows_restart_os as win
    win.protect(tmp_path)
    env = dict(os.environ, RESTART_ACL_PROBE=str(tmp_path))
    output = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
        '$a=Get-Acl -LiteralPath $env:RESTART_ACL_PROBE; '
        '@{protected=$a.AreAccessRulesProtected; sids=@($a.Access | ForEach-Object '
        '{ $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value })} | ConvertTo-Json -Compress'], env=env)
    observed = json.loads(output)
    assert observed['protected'] is True
    assert set(observed['sids']) == {win.user_sid(), 'S-1-5-18'}


def test_health_refuses_malformed_port_before_network(monkeypatch):
    from src import windows_app_restart as control
    def forbidden(*args, **kwargs):
        raise AssertionError('Must reject before network setup')
    monkeypatch.setattr(control.urllib.request, 'build_opener', forbidden)
    with pytest.raises(ValueError, match='numeric'):
        control.health('9130@invalid.example')


def test_invalid_deadlines_are_rejected_before_control_io(tmp_path):
    from src import windows_app_restart as control
    for timeout in (float('nan'), float('inf'), -1):
        with pytest.raises(ValueError, match='timeout'):
            control.request_control(tmp_path, 'stop', timeout=timeout)
        with pytest.raises(ValueError, match='timeout'):
            control.restart(tmp_path, timeout=timeout)
