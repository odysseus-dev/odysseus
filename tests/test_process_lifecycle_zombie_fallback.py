"""Exit observation on POSIX hosts without procfs must recognise zombies."""
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src import process_lifecycle as lifecycle
from src import process_ownership


@pytest.mark.parametrize('status,expected', [('Z', True), (' Z+\n', True), ('ZN', True), ('S', False), ('R+', False), ('', False)])
def test_ps_status_without_procfs(monkeypatch, status, expected):
    monkeypatch.setattr(lifecycle.platform_compat, 'has_procfs', lambda: False)
    monkeypatch.setattr(lifecycle, 'IS_WINDOWS', False)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=status)

    monkeypatch.setattr(lifecycle.subprocess, 'run', run)
    assert lifecycle.is_zombie(1234) is expected
    if calls:
        assert len(calls) == 1
        argv, options = calls[0]
        assert argv == ['ps', '-p', '1234', '-o', 'stat=']
        assert options['timeout'] > 0
        assert options['stderr'] == subprocess.DEVNULL


@pytest.mark.parametrize('failure', [FileNotFoundError('ps'), PermissionError('denied'), subprocess.TimeoutExpired('ps', 1)])
def test_ps_failure_is_not_proof_of_exit(monkeypatch, failure):
    monkeypatch.setattr(lifecycle.platform_compat, 'has_procfs', lambda: False)
    monkeypatch.setattr(lifecycle, 'IS_WINDOWS', False)

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(lifecycle.subprocess, 'run', run)
    assert lifecycle.is_zombie(1234) is False


def test_failed_ps_output_is_not_proof_of_exit(monkeypatch):
    monkeypatch.setattr(lifecycle.platform_compat, 'has_procfs', lambda: False)
    monkeypatch.setattr(lifecycle, 'IS_WINDOWS', False)
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='Z'))
    assert lifecycle.is_zombie(1234) is False


@pytest.mark.parametrize('pid', [None, 0, -1, 'invalid'])
def test_invalid_pids_do_not_launch_ps(monkeypatch, pid):
    monkeypatch.setattr(lifecycle.platform_compat, 'has_procfs', lambda: False)
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda *a, **k: pytest.fail('invalid pid inspection'))
    assert lifecycle.is_zombie(pid) is False


def test_windows_does_not_use_posix_ps(monkeypatch):
    monkeypatch.setattr(lifecycle.platform_compat, 'has_procfs', lambda: False)
    monkeypatch.setattr(lifecycle, 'IS_WINDOWS', True)
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda *a, **k: pytest.fail('POSIX ps on Windows'))
    assert lifecycle.is_zombie(1234) is False


@pytest.mark.parametrize('state,expected', [('Z', True), ('S', False)])
def test_procfs_keeps_its_existing_state_probe(monkeypatch, tmp_path, state, expected):
    stat = tmp_path / '1234' / 'stat'
    stat.parent.mkdir()
    stat.write_text(f'1234 (name with ) parentheses) {state} 1 2 3', encoding='utf-8')
    monkeypatch.setattr(lifecycle.platform_compat, 'has_procfs', lambda: True)
    monkeypatch.setattr(lifecycle.platform_compat, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda *a, **k: pytest.fail('procfs launched ps'))
    assert lifecycle.is_zombie(1234) is expected


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signal and zombie semantics')
def test_real_unreaped_child_is_not_a_survivor_and_bystander_lives():
    code = "import time; print('ready',flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    bystander = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        assert bystander.stdout.readline().strip() == 'ready'
        identity = lifecycle.ProcessIdentity.capture(child.pid)
        assert identity.verdict() == process_ownership.OWNED
        sweep = lifecycle.terminate_identities([identity], steps=[(signal.SIGTERM, 1.0)])
        # Do not poll/wait the target before checking: that would reap the zombie
        # and conceal the native macOS regression.
        assert sweep.killed == (child.pid,)
        assert sweep.survivors == sweep.unverified == ()
        assert identity.exited()
        assert bystander.poll() is None
        assert child.wait(timeout=3) == -signal.SIGTERM
    finally:
        for proc in (child, bystander):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
            proc.stdout.close()
