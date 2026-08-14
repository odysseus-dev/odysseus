"""Executable regressions for the browser/run-lifecycle review of PR #6020.

These tests intentionally exercise JavaScript under Node rather than treating
``node --check`` or source-string presence as proof that the browser paths are
usable.  The detached-run replacement case drives the real Python manager.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from src import agent_runs


_REPO = Path(__file__).resolve().parents[1]
_CHAT_PATH = _REPO / "static" / "js" / "chat.js"
_CHAT = _CHAT_PATH.read_text(encoding="utf-8")
_RENDERER = (_REPO / "static" / "js" / "chatRenderer.js").read_text(
    encoding="utf-8"
)
_STREAM_ERRORS_URI = (_REPO / "static" / "js" / "chatStreamErrors.js").as_uri()
_HAS_NODE = shutil.which("node") is not None


def _extract_source(source: str, start: str, end: str) -> str:
    """Slice module source between two anchors, failing loudly if one moved.

    The extracted region ships to Node verbatim, so the anchors must stay
    unique strings in the module. A refactor that renames or duplicates an
    anchor fails here with the anchor named, not with an opaque split error.
    """
    assert source.count(start) == 1, f"start anchor not unique in source: {start!r}"
    tail = source.split(start, 1)[1]
    assert end in tail, f"end anchor not found after start anchor: {end!r}"
    return start + tail.split(end, 1)[0]


def _run_node(source: str) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # A few imported browser modules log optional-service status at startup.
    # Keep the runtime smoke honest while reading only its final JSON result.
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _chat_smoke_source(extra_source: str) -> str:
    """Return chat.js source with its real imports made absolute."""

    def absolute_import(match: re.Match[str]) -> str:
        relative = match.group("relative")
        path_part, separator, query = relative.partition("?")
        target = (_CHAT_PATH.parent / path_part).resolve().as_uri()
        if separator:
            target += "?" + query
        return match.group("prefix") + target + match.group("quote")

    source = re.sub(
        r"(?P<prefix>from\s+(?P<quote>['\"]))(?P<relative>\./[^'\"]+)(?P=quote)",
        absolute_import,
        _CHAT,
    )
    source += "\n" + extra_source
    return source


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_chat_runtime_stream_state_helpers_are_all_callable(tmp_path):
    """Import the real module and execute all three rebased-away helpers."""

    module_source = _chat_smoke_source(
        """
export function __pr6020StreamStateSmoke() {
  const sid = 'pr6020-runtime-smoke';
  const controller = { abort() {}, signal: { aborted: false } };
  const originalGetSessionId = sessionModule.getCurrentSessionId;
  sessionModule.getCurrentSessionId = () => sid;
  try {
    _activeStreams.set(sid, {
      abortCtrl: controller,
      holder: { id: 'holder' },
      lastActivity: 0,
    });
    const active = _getForegroundStreamState();
    const touchedAt = _touchStreamActivity(sid);
    const synced = _syncForegroundStreamGlobals();
    return {
      activeController: active && active.abortCtrl === controller,
      touched: touchedAt > 0 && _activeStreams.get(sid).lastActivity === touchedAt,
      synced: synced === active && currentAbort === controller && isStreaming,
    };
  } finally {
    _activeStreams.delete(sid);
    sessionModule.getCurrentSessionId = originalGetSessionId;
  }
}
"""
    )
    module_path = tmp_path / "chat-runtime-smoke.mjs"
    module_path.write_text(module_source, encoding="utf-8")
    module_uri = module_path.as_uri()
    script = f"""
      globalThis.window = globalThis;
      globalThis.addEventListener = () => {{}};
      globalThis.removeEventListener = () => {{}};
      globalThis.dispatchEvent = () => {{}};
      globalThis.requestAnimationFrame = () => 0;
      globalThis.cancelAnimationFrame = () => {{}};
      globalThis.fetch = async () => ({{
        ok: false,
        json: async () => ({{}}),
        text: async () => '',
        headers: {{ get() {{ return null; }} }},
      }});
      class Element {{
        constructor() {{
          this.children = [];
          this.classList = {{
            add() {{}}, remove() {{}}, toggle() {{}}, contains() {{ return false; }},
          }};
          this.style = {{ setProperty() {{}} }};
          this.dataset = {{}};
        }}
        querySelector() {{ return null; }}
        querySelectorAll() {{ return []; }}
        appendChild(child) {{ this.children.push(child); return child; }}
        addEventListener() {{}}
        removeEventListener() {{}}
      }}
      class HTMLInputElement extends Element {{
        get value() {{ return this._value || ''; }}
        set value(value) {{ this._value = value; }}
      }}
      globalThis.HTMLInputElement = HTMLInputElement;
      const root = new Element();
      globalThis.document = {{
        body: root,
        head: root,
        documentElement: root,
        getElementById() {{ return null; }},
        querySelector() {{ return null; }},
        querySelectorAll() {{ return []; }},
        createElement(tag) {{ return tag === 'input' ? new HTMLInputElement() : new Element(); }},
        createTextNode(text) {{ return {{ textContent: text }}; }},
        addEventListener() {{}},
        removeEventListener() {{}},
      }};
      globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}}, removeItem() {{}} }};
      globalThis.location = {{}};
      globalThis.history = {{}};
      globalThis.MutationObserver = class {{ observe() {{}} }};
      globalThis.CustomEvent = class {{}};
      globalThis.Storage = class {{}};
      const chat = await import({json.dumps(module_uri)});
      console.log(JSON.stringify(chat.__pr6020StreamStateSmoke()));
      process.exit(0);
    """

    assert _run_node(script) == {
        "activeController": True,
        "touched": True,
        "synced": True,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_canonical_terminal_followed_by_eof_is_not_auto_recovered():
    """A persisted terminal marker owns EOF; a plain premature EOF still retries."""

    completion_gate = _extract_source(
        _CHAT,
        "if (_streamTerminalError)",
        "// The final foreground render below is authoritative.",
    )
    script = f"""
      import {{
        createTerminalStreamError,
        isRecoverableStreamError,
      }} from {json.dumps(_STREAM_ERRORS_URI)};
      function runCompletionGate(canonicalTerminalSaved) {{
        let _streamTerminalError = null;
        let _streamSawDone = false;
        let _canonicalTerminalSaved = canonicalTerminalSaved;
        try {{
          {completion_gate}
          return {{ recovered: false, completed: true }};
        }} catch (error) {{
          return {{
            recovered: isRecoverableStreamError(error),
            completed: false,
            terminal: !!error.terminalStreamError,
            message: error.message,
          }};
        }}
      }}
      console.log(JSON.stringify({{
        savedTerminal: runCompletionGate(true),
        plainEof: runCompletionGate(false),
      }}));
    """

    assert _run_node(script) == {
        # A saved canonical terminal must neither auto-recover nor render as a
        # clean success: it takes the terminal-error path, whose catch handler
        # reloads the persisted record.
        "savedTerminal": {
            "recovered": False,
            "completed": False,
            "terminal": True,
            "message": "Stream closed after canonical terminal event",
        },
        "plainEof": {
            "recovered": True,
            "completed": False,
            "terminal": False,
            "message": "Stream closed before completion",
        },
    }


@pytest.mark.asyncio
async def test_immediate_replacement_closes_subscriber_bound_to_never_started_run():
    """Cancellation before _drain's first instruction must still terminalize run 1."""

    session_id = "pr6020-immediate-replacement"
    agent_runs._RUNS.pop(session_id, None)

    async def never_started():
        yield 'data: {"delta":"old"}\n\n'

    async def replacement():
        yield 'data: {"delta":"new"}\n\n'

    first = agent_runs.start(session_id, never_started())
    first_subscription = asyncio.create_task(
        _collect_run_events(session_id, first)
    )
    # Do not yield between starts: first.task is cancelled before _drain gets
    # its first instruction, exactly the race a rapid double-send creates.
    second = agent_runs.start(session_id, replacement())

    assert await asyncio.wait_for(first_subscription, timeout=0.5) == []
    await asyncio.wait_for(second.task, timeout=0.5)
    assert first.status == "stopped"
    assert second.status == "done"


async def _collect_run_events(session_id: str, run: object) -> list[str]:
    return [event async for event in agent_runs.subscribe(session_id, run)]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_stop_before_response_headers_waits_for_exact_run_identity():
    """Never send headerless Stop, but flush the queued Stop once headers arrive."""

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    header_capture = _extract_source(
        _CHAT,
        "const streamRunId = res.headers.get('X-Odysseus-Run-Id')",
        "// Mark the chat log busy",
    )
    script = f"""
      const calls = [];
      function _setForegroundChatBusy() {{}}
      const window = {{}};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      const fetch = async (url, options) => {{ calls.push({{ url, options }}); return {{ ok: true }}; }};
      {state_and_stop}
      {{
        const streamSessionId = 'normal-session';
        const res = {{ headers: {{ get(name) {{
          return name === 'X-Odysseus-Run-Id' ? 'normal-run' : null;
        }} }} }};
        {header_capture}
      }}
      await new Promise(resolve => setTimeout(resolve, 0));
      const normalHeaderCalls = calls.length;
      let beforeHeaders;
      {{
        const streamSessionId = 'session-1';
        _stopExactRun(streamSessionId);
        beforeHeaders = calls.length;
        const res = {{ headers: {{ get(name) {{
          return name === 'X-Odysseus-Run-Id' ? 'run-1' : null;
        }} }} }};
        {header_capture}
      }}
      await new Promise(resolve => setTimeout(resolve, 0));
      console.log(JSON.stringify({{
        normalHeaderCalls,
        beforeHeaders,
        calls: calls.map(call => ({{
          url: call.url,
          method: call.options.method,
          runId: call.options.headers['X-Odysseus-Run-Id'],
        }})),
      }}));
    """

    assert _run_node(script) == {
        "normalHeaderCalls": 0,
        "beforeHeaders": 0,
        "calls": [
            {
                "url": "/api/chat/stop/session-1",
                "method": "POST",
                "runId": "run-1",
            }
        ],
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_timeout_before_response_headers_also_waits_for_exact_run_identity():
    """The automatic timeout must preserve the POST until its run id arrives."""

    script = f"""
      {_timeout_harness_prelude()}
      callbacks[0]();
      const beforeHeaders = {{ aborted: abortCtrl.signal.aborted, calls: calls.length }};
      _rememberStreamRunId(streamSessionId, 'run-1');
      await Promise.resolve();
      console.log(JSON.stringify({{
        beforeHeaders,
        afterHeaders: {{
          aborted: abortCtrl.signal.aborted,
          runId: calls[0] && calls[0].options.headers['X-Odysseus-Run-Id'],
        }},
      }}));
    """

    assert _run_node(script) == {
        "beforeHeaders": {"aborted": False, "calls": 0},
        "afterHeaders": {"aborted": True, "runId": "run-1"},
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_aborts_stale_queued_stop_instead_of_dropping_it():
    """A new send must honor a Stop still queued against the previous POST."""

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    resend_reset = _extract_source(
        _CHAT, "_streamSessionId = streamSessionId;", "const streamQuery = msg;"
    )
    script = f"""
      const calls = [];
      function _setForegroundChatBusy() {{}}
      const window = {{}};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      const fetch = async (url, options) => {{ calls.push({{ url, options }}); return {{ ok: true }}; }};
      {state_and_stop}
      const oldCtrl = {{
        _reason: '',
        signal: {{ aborted: false }},
        abort() {{ this.signal.aborted = true; }},
      }};
      _stopExactRun('session-1', oldCtrl);
      const queuedBefore = _pendingRunStops.has('session-1');
      {{
        const streamSessionId = 'session-1';
        {resend_reset}
      }}
      console.log(JSON.stringify({{
        queuedBefore,
        queuedAfter: _pendingRunStops.has('session-1'),
        oldControllerAborted: oldCtrl.signal.aborted,
        oldControllerReason: oldCtrl._reason,
        stopCalls: calls.length,
      }}));
    """

    assert _run_node(script) == {
        "queuedBefore": True,
        "queuedAfter": False,
        # The queued cancellation is honored by aborting the superseded POST,
        # never silently dropped and never sent as a headerless server stop.
        # The reason marks it a deliberate stop so the old stream's catch does
        # not render it as an unexpected error.
        "oldControllerAborted": True,
        "oldControllerReason": "user-stop",
        "stopCalls": 0,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_superseded_stream_cleanup_leaves_replacement_registration_alone():
    """A stale stream's finally must not clear state a replacement now owns."""

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    finally_cleanup = _extract_source(
        _CHAT,
        "const _finallyRegistered = _activeStreams.get(streamSessionId);",
        "// Streaming done — let screen readers announce",
    )
    script = f"""
      let currentAbort = null;
      let isStreaming = false;
      let currentHolder = null;
      let _sendInFlight = false;
      function _setForegroundChatBusy() {{}}
      const window = {{}};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      {state_and_stop}
      function runCleanup(abortCtrl) {{
        const streamSessionId = 'session-1';
        {finally_cleanup}
        return _ownsStreamState;
      }}
      const oldCtrl = {{ signal: {{ aborted: true }}, abort() {{}} }};
      const newCtrl = {{ signal: {{ aborted: false }}, abort() {{}} }};
      // Superseded: a replacement stream registered for the same session
      // while the old stream's cleanup was still queued.
      _streamSessionId = 'session-1';
      _activeStreams.set('session-1', {{ abortCtrl: newCtrl, holder: null, lastActivity: 1 }});
      _pendingRunStops.set('session-1', newCtrl);
      const supersededOwns = runCleanup(oldCtrl);
      const afterSuperseded = {{
        registered: _activeStreams.has('session-1'),
        pendingKept: _pendingRunStops.has('session-1'),
        sessionKept: _streamSessionId === 'session-1',
      }};
      // Owner: the registered controller cleans up normally.
      const ownerOwns = runCleanup(newCtrl);
      const afterOwner = {{
        registered: _activeStreams.has('session-1'),
        pendingKept: _pendingRunStops.has('session-1'),
        sessionCleared: _streamSessionId === null,
      }};
      console.log(JSON.stringify({{ supersededOwns, afterSuperseded, ownerOwns, afterOwner }}));
    """

    assert _run_node(script) == {
        "supersededOwns": False,
        "afterSuperseded": {
            "registered": True,
            "pendingKept": True,
            "sessionKept": True,
        },
        "ownerOwns": True,
        "afterOwner": {
            "registered": False,
            "pendingKept": False,
            "sessionCleared": True,
        },
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_timeout_grace_hard_aborts_when_run_identity_never_arrives():
    """A POST hung before headers is still cancelled by the timeout's grace."""

    script = f"""
      {_timeout_harness_prelude()}
      callbacks[0]();
      const afterTimeout = {{ aborted: abortCtrl.signal.aborted, pending: callbacks.length }};
      callbacks[1]();
      console.log(JSON.stringify({{
        afterTimeout,
        afterGrace: {{ aborted: abortCtrl.signal.aborted, stopCalls: calls.length }},
      }}));
    """

    assert _run_node(script) == {
        "afterTimeout": {"aborted": False, "pending": 2},
        "afterGrace": {"aborted": True, "stopCalls": 0},
    }


def _timeout_harness_prelude() -> str:
    """Shared Node harness: real stop/state and timeout blocks, fake timers."""

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    timeout_setup = _extract_source(
        _CHAT, "timeoutId = setTimeout(() =>", "}, timeoutMs);"
    ) + "}, timeoutMs);"
    return f"""
      const calls = [];
      const callbacks = [];
      function _setForegroundChatBusy() {{}}
      function setTimeout(callback) {{ callbacks.push(callback); return 1; }}
      const window = {{}};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      const fetch = async (url, options) => {{ calls.push({{ url, options }}); return {{ ok: true }}; }};
      const RUN_ID_ABORT_GRACE_MS = 2000;
      {state_and_stop}
      const streamSessionId = 'session-1';
      const timeoutMs = 1;
      let timeoutId;
      let timedOut = false;
      const abortCtrl = {{
        _reason: '',
        signal: {{ aborted: false }},
        abort() {{ this.signal.aborted = true; }},
      }};
      {timeout_setup}
    """


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_cost_ledger_serializes_stale_cross_tab_writers():
    """A stale writer must merge, not overwrite a distinct run recorded by a peer."""

    ledger = _extract_source(
        _RENDERER, "const _COST_KEY", "/** Create a timestamp span"
    ).replace("export function", "function")
    script = f"""
      const state = {{}};
      let triggerPeerWrite = true;
      let lockTail = Promise.resolve();
      const navigator = {{ locks: {{
        request(_name, callback) {{
          const next = lockTail.then(callback);
          lockTail = next.catch(() => {{}});
          return next;
        }},
      }} }};
      const window = {{ sessionModule: {{ getCurrentSessionId() {{ return 'session'; }} }} }};
      const document = {{ getElementById() {{ return null; }} }};
      function _metricsBillableCost(metrics) {{ return metrics.testCost; }}
      const peerMetrics = {{ testCost: 0.22, _costRecordId: 'run-b' }};
      let tabA;
      let tabB;
      const localStorage = {{
        getItem(key) {{
          const staleSnapshot = state[key] || null;
          if (key === 'ody-session-cost-runs' && triggerPeerWrite) {{
            triggerPeerWrite = false;
            tabB.recordSessionMetricsCost(peerMetrics, 'session');
          }}
          return staleSnapshot;
        }},
        setItem(key, value) {{ state[key] = value; }},
      }};
      function createTab() {{
        {ledger}
        return {{ recordSessionMetricsCost }};
      }}
      tabA = createTab();
      tabB = createTab();
      const metricsA = {{ testCost: 0.11, _costRecordId: 'run-a' }};
      tabA.recordSessionMetricsCost(metricsA, 'session');
      const queued = {{
        recorded: !!metricsA._costRecorded,
        pending: !!metricsA._costRecordPending,
      }};
      await new Promise(resolve => setTimeout(resolve, 0));
      await lockTail;
      const runs = JSON.parse(state['ody-session-cost-runs'] || '{{}}').session || {{}};
      console.log(JSON.stringify({{
        queued,
        settled: {{
          recorded: !!metricsA._costRecorded,
          pending: !!metricsA._costRecordPending,
        }},
        runs,
      }}));
    """

    assert _run_node(script) == {
        # Recorded must not be claimed while the write only sits queued behind
        # the lock; it flips once the write has actually run.
        "queued": {"recorded": False, "pending": True},
        "settled": {"recorded": True, "pending": False},
        "runs": {"run-a": 0.11, "run-b": 0.22},
    }
