"""History controllers must not dismiss reports the server failed to delete."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(outcomes, *, bulk=False, clear=False, statuses=None):
    if not shutil.which("node"):
        pytest.skip("node is required to execute research controllers")
    script = r"""
const fs = require('fs'), vm = require('vm');
const options = JSON.parse(process.argv[1]);
const saved = new Map();
const errors = [], calls = [];
const context = {
  localStorage: {getItem:k=>saved.get(k)||null, setItem:(k,v)=>saved.set(k,v)},
  document: {getElementById:()=>null},
  uiModule: {showError:message=>errors.push(message)},
  window: {styledConfirm:async()=>true},
  fetch: async (url, opts) => {
    const id = url.split('/').pop();calls.push({id, method:opts.method, credentials:opts.credentials});
    const outcome = options.outcomes[id] ?? 200;
    if (outcome === 'network') throw new Error('scripted network failure');
    return {ok:outcome>=200 && outcome<300, status:outcome};
  },
};
vm.createContext(context);
const jobs = fs.readFileSync('static/js/research/jobs.js','utf8');
vm.runInContext(jobs.replace(/export /g, ''), context);
vm.runInContext('globalThis.jobs = {getJobs,removeJob};', context);
context.seed = Object.entries(options.statuses || {a:'done',b:'done',untouched:'done'}).map(([id,status])=>({id,status}));
vm.runInContext('getJobs().push(...seed);', context);
const panel = fs.readFileSync('static/js/research/panel.js','utf8');
const start = panel.indexOf('function _updateHistoryBulkBar(');
const end = panel.indexOf('function _renderJobs(', start);
if (start < 0 || end < 0) throw new Error('history controller boundaries missing');
vm.runInContext("_apiBase=''; let _historySelectMode=true; let _historySelectedIds=new Set(['a','b']); let _visibleHistoryIds=['a','b']; function _renderJobs(){};"+panel.slice(start,end), context);
(async()=>{
  const command = options.clear ? '_clearHistory()' : options.bulk ? '_bulkDeleteHistory()' : "_deleteHistoryJobs(['a','b'])";
  await vm.runInContext(command, context);
  console.log(JSON.stringify({
    ids:vm.runInContext('getJobs().map(j=>j.id)',context),
    selected:vm.runInContext('[..._historySelectedIds]',context),
    selectionMode:vm.runInContext('_historySelectMode',context),
    dismissed:JSON.parse(saved.get('odysseus-research-dismissed')||'[]'),
    errors,calls,
  }));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    args = dict(outcomes=outcomes, bulk=bulk, clear=clear, statuses=statuses)
    result = subprocess.run(["node", "-e", script, json.dumps(args)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("failure", [403, 404, 500, 503, "network"])
def test_failed_reports_remain_visible_and_undismissed(failure):
    result = _run({"a": failure, "b": failure})
    assert result["ids"] == ["a", "b", "untouched"]
    assert result["dismissed"] == []
    assert len(result["errors"]) == 1
    assert "2" in result["errors"][0]
    assert all(call["method"] == "DELETE" and call["credentials"] == "same-origin"
               for call in result["calls"])


@pytest.mark.parametrize("failure", [500, "network"])
def test_mixed_bulk_deletion_keeps_only_failed_selection_for_retry(failure):
    result = _run({"a": 200, "b": failure}, bulk=True)
    assert result["ids"] == ["b", "untouched"]
    assert result["dismissed"] == ["a"]
    assert result["selectionMode"] is True
    assert result["selected"] == ["b"]
    assert len(result["errors"]) == 1


def test_successful_bulk_deletion_exits_selection():
    result = _run({"a": 200, "b": 200}, bulk=True)
    assert result["ids"] == ["untouched"]
    assert result["selectionMode"] is False
    assert result["selected"] == []
    assert result["dismissed"] == ["a", "b"]
    assert result["errors"] == []


def test_local_failed_job_removal_needs_no_server_request():
    result = _run({"a": 500}, statuses={"a": "error", "b": "cancelled"})
    assert result["ids"] == []
    assert result["calls"] == []
    assert result["errors"] == []


def test_clear_history_preserves_running_jobs_and_failed_deletions():
    result = _run({"a": 500}, clear=True, statuses={"a": "done", "b": "running"})
    assert result["ids"] == ["a", "b"]
    assert result["dismissed"] == []
    assert [call["id"] for call in result["calls"]] == ["a"]
