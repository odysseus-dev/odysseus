"""Actual Windows listener observation with a tiny stdlib peer, never inference."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from tests.test_model_serving_control import plan_for

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native TCP table attribution')


@pytest.mark.asyncio
async def test_status_attributes_only_verified_direct_controller_listener(tmp_path, monkeypatch):
    from src import model_serving_control as control
    from src.model_serving_status import local_serving_status
    root = Path(__file__).resolve().parents[1]
    children = []
    def spawn(plan, log):
        child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), str(root / 'tests/helpers/serving_runtime_fixture.py'),
            '--port', str(plan['port'])], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(child)
        return child
    monkeypatch.setattr(control, '_spawn_runtime', spawn)
    manager = control.LocalServingController(tmp_path / 'state.json', tmp_path / 'logs')
    started = manager.start(plan_for(control, tmp_path))
    sid = started['session_id']
    row = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = await local_serving_status(manager.state_path, manager.log_dir)
            row = next(s for s in status['services'] if s['id'] == sid)
            if row['readiness'] == 'ready':
                break
            await asyncio.sleep(.02)
        assert row is not None and row['readiness'] == 'ready'
        assert row['endpoint_ownership_verified'] is True
        assert row['controls']['stop'] is True
        from src.listener_attribution import listener_matches_process
        receipt = manager._receipt(sid)
        assert receipt is not None
        assert listener_matches_process(row['base_url'], os.getpid(), receipt['start_token']) is False
        assert listener_matches_process(row['base_url'], receipt['pid'], 'foreign-incarnation') is False
        assert listener_matches_process('http://example.com:8080', receipt['pid'], receipt['start_token']) is False
    finally:
        manager.stop(sid)
        for child in children:
            child.wait(timeout=5)
