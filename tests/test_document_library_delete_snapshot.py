"""A confirmed delete batch must keep its identity across async UI work."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is required")


def run_batch(mutation, stage="request", accepted=True, failure=False, selected=True):
    source = (ROOT / "static/js/documentLibrary.js").read_text()
    function = source[source.index("  async function libraryBulkDelete() {"):
                      source.index("  async function libraryBulkArchive() {")]
    setup = json.dumps(dict(mutation=mutation, stage=stage, accepted=accepted,
                            failure=failure, selected=selected))
    script = """
const assert = require('node:assert/strict');
const options = OPTIONS;
const _librarySelectedIds = new Set(options.selected ? ['first', 'second'] : []);
const calls = [], notices = [];
let confirmation, exits = 0, refreshes = 0;
const API_BASE = '';
const document = {querySelector: () => null};
const CSS = {escape: value => value};
const console = {warn() {}, error() {}};
const setTimeout = callback => { callback(); };
function mutate() {
  if (options.mutation === 'add') _librarySelectedIds.add('outside');
  if (options.mutation === 'remove') _librarySelectedIds.delete('second');
  if (options.mutation === 'clear') _librarySelectedIds.clear();
  if (options.mutation === 'replace') {
    _librarySelectedIds.clear(); _librarySelectedIds.add('outside');
  }
}
const uiModule = {
  async styledConfirm(message) {
    confirmation = message;
    if (options.stage === 'confirm') mutate();
    await Promise.resolve();
    return options.accepted;
  },
  showError: message => notices.push(['error', message]),
  showToast: message => notices.push(['success', message]),
};
async function fetch(url) {
  calls.push(url.split('/').at(-1));
  if (calls.length === 1 && options.stage === 'request') mutate();
  await Promise.resolve();
  return {ok: !(options.failure && calls.length === 1), status: 500};
}
function libraryExitSelectMode() { exits++; _librarySelectedIds.clear(); }
async function libraryFetch() { refreshes++; }
FUNCTION
(async () => {
  await libraryBulkDelete();
  process.stdout.write(JSON.stringify({calls, notices, confirmation, exits, refreshes}));
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
""".replace("OPTIONS", setup).replace("FUNCTION", function)
    result = subprocess.run([NODE], input=script, text=True, capture_output=True,
                            cwd=ROOT, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("stage", ["confirm", "request"])
@pytest.mark.parametrize("mutation", ["add", "remove", "clear", "replace"])
def test_confirmed_batch_is_independent_of_later_selection(stage, mutation):
    result = run_batch(mutation, stage)
    assert result["confirmation"] == "Delete 2 documents?"
    assert result["calls"] == ["first", "second"]
    assert result["notices"] == [["success", "Deleted 2 documents"]]
    assert result["exits"] == result["refreshes"] == 1


def test_failed_request_does_not_allow_later_selection_into_batch():
    result = run_batch("add", failure=True)
    assert result["calls"] == ["first", "second"]
    assert result["notices"] == [["error", "Deleted 1 · 1 failed"]]


def test_unchanged_selection_deletes_the_confirmed_items():
    assert run_batch("none")["calls"] == ["first", "second"]


def test_cancelled_confirmation_sends_no_delete():
    result = run_batch("add", stage="confirm", accepted=False)
    assert result["calls"] == result["notices"] == []
    assert result["exits"] == result["refreshes"] == 0


def test_empty_selection_does_not_prompt_or_send():
    result = run_batch("none", selected=False)
    assert "confirmation" not in result
    assert result["calls"] == result["notices"] == []
