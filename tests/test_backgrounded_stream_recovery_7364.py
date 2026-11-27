"""Regression tests for backgrounded-page stream recovery (#7364)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHAT_JS = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")


def _extract_section(start_marker: str, end_marker: str) -> str:
    start = CHAT_JS.index(start_marker)
    end = CHAT_JS.index(end_marker, start)
    return CHAT_JS[start:end]


def test_backgrounded_page_recovery_hooks_present() -> None:
    vis = _extract_section(
        "document.addEventListener('visibilitychange', () => {",
        "// On mobile, fade out welcome text",
    )
    assert "BACKGROUND_HIDDEN_REATTACH_MS" in vis
    assert "BACKGROUND_RETURN_SILENCE_MS" in vis
    assert "sessionModule.selectSession(sid, { showLoading: false })" in vis

    auto_recover = _extract_section(
        "function _tryAutoRecover(holder, accumulated, sessionId)",
        "function _removeStallBanner()",
    )
    assert "/api/chat/stream_status/${encodeURIComponent(sessionId)}" in auto_recover
    assert "sessionModule.selectSession(sessionId, { showLoading: false })" in auto_recover

    probe = _extract_section(
        "async function _probeStaleLocalStream()",
        "function _startStallWatchdog()",
    )
    assert "sessionModule.selectSession(sid, { showLoading: false })" in probe


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js runtime not available")
def test_backgrounded_stream_recovery_paths_in_node_vm() -> None:
    auto_recover_src = _extract_section(
        "function _tryAutoRecover(holder, accumulated, sessionId)",
        "function _removeStallBanner()",
    )
    probe_src = _extract_section(
        "async function _probeStaleLocalStream()",
        "function _startStallWatchdog()",
    )
    vis_src = _extract_section(
        "document.addEventListener('visibilitychange', () => {",
        "// On mobile, fade out welcome text",
    )

    harness = f"""
const assert = require('assert');
const vm = require('vm');

const listeners = {{}};
const reentered = [];
const typedErrors = [];
let now = 100000;

const realSetTimeout = setTimeout;
const realClearTimeout = clearTimeout;
const scheduled = [];

function fakeSetTimeout(fn, ms) {{
  const item = {{ fn, ms, cleared: false }};
  scheduled.push(item);
  return item;
}}
function fakeClearTimeout(item) {{
  if (item) item.cleared = true;
}}

const activeStream = {{
  abortCtrl: {{ signal: {{ aborted: false }}, abort() {{ this.signal.aborted = true; }} }},
  lastActivity: 100000,
}};

const _activeStreams = new Map([['sess-1', activeStream]]);
const _backgroundStreams = new Map();
let _autoNudges = 0;
const _AUTO_NUDGE_CAP = 3;
let _staleStreamProbeInFlight = false;
const STALE_LOCAL_STREAM_MS = 15000;
const BACKGROUND_HIDDEN_REATTACH_MS = 10000;
const BACKGROUND_RETURN_SILENCE_MS = 3000;
let _hiddenAt = 0;
let _bgReturnReenterTimer = null;
let _lastReaderActivity = 100000;
let _sendInFlight = true;
let _webLockRelease = null;
const API_BASE = '';
let fetchImpl = async () => ({{ status: 404, ok: false, json: async () => ({{}}) }});
let resumeImpl = async () => false;

const sandbox = {{
  Date: {{ now: () => now }},
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  console,
  encodeURIComponent,
  API_BASE,
  _activeStreams,
  _backgroundStreams,
  _autoNudges,
  _AUTO_NUDGE_CAP,
  _staleStreamProbeInFlight,
  STALE_LOCAL_STREAM_MS,
  BACKGROUND_HIDDEN_REATTACH_MS,
  BACKGROUND_RETURN_SILENCE_MS,
  _hiddenAt,
  _bgReturnReenterTimer,
  _lastReaderActivity,
  _sendInFlight,
  _webLockRelease,
  document: {{
    visibilityState: 'visible',
    addEventListener(type, fn) {{ listeners[type] = fn; }},
    getElementById() {{ return null; }},
    querySelector() {{ return null; }},
  }},
  uiModule: {{
    el() {{ return {{ disabled: true }}; }},
    scrollHistory() {{}},
  }},
  sessionModule: {{
    getCurrentSessionId() {{ return 'sess-1'; }},
    async selectSession(sid, opts) {{ reentered.push({{ sid, opts }}); }},
  }},
  fetch: (...args) => fetchImpl(...args),
  resumeStream: (...args) => resumeImpl(...args),
  typewriterInto(body, text) {{ typedErrors.push(text); }},
  _getForegroundStreamState() {{ return _activeStreams.get('sess-1') || null; }},
  _syncForegroundStreamGlobals() {{}},
  updateSubmitButton() {{}},
  _drainQueuedAgentRequests() {{}},
  _restoreQueuedRequestsForCurrentSession() {{}},
}};

vm.createContext(sandbox);
vm.runInContext({json.dumps(auto_recover_src + "\n" + probe_src + "\n" + vis_src)}, sandbox);

(async () => {{
  // Case 1: Page hidden for 12s, returns with dead foreground stream (no data for 3s) -> selectSession('sess-1')
  sandbox.document.visibilityState = 'hidden';
  listeners.visibilitychange();
  now += 12000;
  sandbox.document.visibilityState = 'visible';
  listeners.visibilitychange();

  const returnTimer = scheduled.find(t => !t.cleared && t.ms === 3000);
  assert(returnTimer, 'Expected 3s foreground silence timer after >=10s hidden');
  await returnTimer.fn();
  assert.strictEqual(reentered.length, 1);
  assert.strictEqual(reentered[0].sid, 'sess-1');
  assert.strictEqual(reentered[0].opts.showLoading, false);

  // Case 2: _tryAutoRecover when resumeStream fails and stream_status is 404 -> selectSession instead of Connection lost
  const holder = {{
    dataset: {{}},
    isConnected: true,
    querySelector() {{ return {{}}; }},
  }};
  const ok = sandbox._tryAutoRecover(holder, 'partial text', 'sess-1');
  assert.strictEqual(ok, true);
  const recoverTimer = scheduled.find(t => !t.cleared && t.ms === 200);
  assert(recoverTimer, 'Expected 200ms auto-recover timer');
  await recoverTimer.fn();
  assert.strictEqual(typedErrors.length, 0, 'Should not show Connection lost when server run finished (404)');
  assert.strictEqual(reentered.length, 2);

  // Case 3: _probeStaleLocalStream when stream_status returns 404 -> unlocks and calls selectSession('sess-1')
  now += 20000;
  await sandbox._probeStaleLocalStream();
  assert.strictEqual(reentered.length, 3);
  assert.strictEqual(_activeStreams.has('sess-1'), false);
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Node VM harness failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
