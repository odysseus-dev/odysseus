"""Queued download identities must not enter real-session status monitoring."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_case(data):
    if shutil.which('node') is None:
        pytest.skip('node is not available')
    # Actual full module, with browser/storage/network/clock ports and test-only
    # exports of its private monitors. No queue or reconciliation code replaced.
    harness = (ROOT / 'tests' / 'helpers' / 'cookbook_queued_placeholders.mjs').read_text()
    result = subprocess.run(
        ['node','--experimental-vm-modules','--input-type=module','-e',harness],
        cwd=ROOT,input=json.dumps(data),text=True,capture_output=True,timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def task(sid, status='running'):
    return {'id':sid,'sessionId':sid,'type':'download','status':status,'name':'owned fixture','payload':{'repo_id':'owned/fixture'},'ts':1794526800000}


@pytest.mark.parametrize('status', ['queued','running'])
def test_pending_placeholder_ignores_stopped_backend_report(status):
    result = run_case({'tasks':[task('queue-owned',status)],'live':[{'session_id':'queue-owned','type':'download','status':'stopped'}]})
    assert result['tasks'][0]['status'] == status


def test_real_stopped_download_is_still_marked_crashed():
    result = run_case({'tasks':[task('cookbook-owned')],'live':[{'session_id':'cookbook-owned','type':'download','status':'stopped'}]})
    assert result['tasks'][0]['status'] == 'crashed'


def test_untracked_placeholder_is_not_adopted_as_a_dead_session():
    result = run_case({'live':[{'session_id':'queue-owned','type':'download','status':'stopped'}]})
    assert result['tasks'] == []


def test_real_external_download_is_still_adopted():
    result = run_case({'live':[{'session_id':'cookbook-owned','type':'download','status':'completed'}]})
    assert len(result['tasks']) == 1
    assert result['tasks'][0]['sessionId'] == 'cookbook-owned'
    assert result['tasks'][0]['status'] == 'done'


@pytest.mark.parametrize('status', ['queued','running'])
def test_placeholder_never_starts_tmux_reconnect(status):
    result = run_case({'kind':'reconnect','task':task('queue-owned',status)})
    assert result['calls'] == []


def test_real_identity_still_reconnects_after_launch():
    result = run_case({'kind':'reconnect','task':task('cookbook-owned')})
    assert len(result['calls']) == 1
    assert 'capture-pane -t cookbook-owned ' in result['calls'][0]
