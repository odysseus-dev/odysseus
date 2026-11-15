"""Execute the real background reconciliation, not source-string badge checks."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_js(body):
    result = subprocess.run(["node", "--input-type=module", "-e", body], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("backend", ["error", "completed", "running", "stopped", "cancel_requested", None])
def test_backend_outcome_beats_download_ok(backend):
    result = run_js("""
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('static/js/cookbookRunning.js', 'utf8');
const poll = source.slice(source.indexOf('async function _pollBackgroundStatus()'), source.indexOf('// ── Init:'));
let tasks = [{sessionId:'cookbook-b167833a',type:'download',status:BACKEND===null?'running':'done',output:'DOWNLOAD_OK',payload:{repo_id:'google/gemma'}}];
let renders = 0, notifications = 0, stops = 0;
const noop = () => {};
const ctx = {
  _canBackgroundPoll: () => true, _bgPollInFlight:false,
  _fetchWithTimeout: async url => ({ok:true,json:async()=>url.endsWith('/state')?{tasks:[]}:{tasks:BACKEND===null?[]:[{
    session_id:'cookbook-b167833a',type:'download',status:BACKEND,output_tail:'Fetching 0 files\\nDOWNLOAD_OK'
  }]}}),
  _loadTasks:()=>tasks, _saveTasks:t=>tasks=t, _renderRunningTab:()=>renders++,
  _isQueuedDownloadPlaceholder:()=>false, _isTombstoned:()=>false,
  _depInstallSucceeded:()=>false, _serveOutputLooksReady:()=>false,
  isModelDownloadTask:()=>true, _isServeOutputPlaceholder:()=>false,
  _redactStoredText:s=>s,
  _notifyModelDownloadCompleted:()=>notifications++, _refreshDepsAfterInstall:noop,
  document:{getElementById:()=>null,querySelector:()=>null},
  _showCookbookNotif:noop,_clearCookbookNotif:noop,_processQueue:noop,
  _hasLiveTasks:()=>tasks.some(t=>t.status==='running'),_stopBackgroundMonitor:()=>stops++,
};
vm.createContext(ctx);
await vm.runInContext(poll+'; _pollBackgroundStatus()',ctx);
console.log(JSON.stringify({status:tasks[0].status,renders,notifications,stops}));
""".replace("BACKEND", json.dumps(backend)))
    expected = "running" if backend is None else {"completed": "done"}.get(backend, backend)
    assert result["status"] == expected, result
    if backend is None:
        assert result["stops"] == 0, "initial sync race must not stop monitoring"
    if backend != "completed":
        assert result["notifications"] == 0


def test_every_snapshot_display_path_uses_the_cleaner():
    source = (ROOT / "static/js/cookbookRunning.js").read_text(encoding="utf-8")
    assert "output.textContent = snapshot;" not in source
    assert "output.textContent = _cleanTerminalText(snapshot);" in source


def test_windows_capture_requests_utf8_input_and_output():
    source = (ROOT / "static/js/cookbookRunning.js").read_text(encoding="utf-8")
    assert "-Encoding UTF8 -Tail" in source
    assert "[Console]::OutputEncoding" in source


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='native PowerShell encoding')
def test_real_windows_capture_preserves_retained_gemma_utf8(tmp_path, monkeypatch):
    from tests.test_cookbook_windows_completion import GEMMA_LOG, SID

    logs = tmp_path / 'odysseus-tmux'
    logs.mkdir()
    (logs / f'{SID}.log').write_bytes(GEMMA_LOG.encode('utf-8'))
    monkeypatch.setenv('TEMP', str(tmp_path))
    command = run_js("""
import fs from 'node:fs';
const s = fs.readFileSync('static/js/cookbookRunning.js','utf8');
const start = s.indexOf('function _winSessionCmd(');
const end = s.indexOf('// Stop a recorded PID',start);
const _taskRemoteHost=()=>'', _sshPrefix=()=>'', _getPort=()=>'', _safeSessionId=t=>t.sessionId;
eval(s.slice(start,end) + '; globalThis.cmd = _winSessionCmd({sessionId:"cookbook-b167833a"},"capture-pane -S -500")');
console.log(JSON.stringify(globalThis.cmd));
""")
    # Execute only our checked-in builder with a fixed safe session id; no user
    # command/data is evaluated. shell=True exercises the real Windows shell path.
    result = subprocess.run(command, shell=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    text = result.stdout.decode('utf-8')
    assert 'NOT SET — gated/private' in text
    assert '\x1b[32m✓ Downloaded\x1b[0m' in text
    assert 'D:\\odysseus-models\\hub' in text


def test_storage_preserves_known_token_status_but_redacts_values():
    from tests.test_cookbook_windows_completion import GEMMA_LOG
    result = run_js("""
import fs from 'node:fs'; import vm from 'node:vm';
const s=fs.readFileSync('static/js/cookbookRunning.js','utf8');
const f=s.slice(s.indexOf('function _redactStoredText('),s.indexOf('function _isServeOutputPlaceholder('));
const ctx={}; vm.createContext(ctx); vm.runInContext(f,ctx);
const log=LOG;
console.log(JSON.stringify({status:ctx._redactStoredText(log),secret:ctx._redactStoredText('token=not-a-real-secret')}));
""".replace('LOG', json.dumps(GEMMA_LOG)))
    assert 'HF token: NOT SET' in result['status']
    assert result['secret'] == 'token=[redacted]'


