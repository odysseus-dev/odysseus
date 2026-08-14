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
        const streamGeneration = 1;
        _streamGenerations.set(streamSessionId, streamGeneration);
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
        const streamGeneration = 1;
        _streamGenerations.set(streamSessionId, streamGeneration);
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
      _rememberStreamRunId(streamSessionId, 'run-1', streamGeneration);
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
def test_resend_preserves_queued_stop_until_old_run_identity_arrives():
    """A replacement must not sever the superseded POST's identity channel.

    The queued Stop stays generation-tagged and fires from the OLD send's own
    header arrival, so the old run is cancelled even when the replacement dies
    before its POST reaches the server (which is what would otherwise cancel
    it). The old run id must not leak into the replacement's identity map.
    """

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    resend_reset = _extract_source(
        _CHAT,
        "const streamGeneration = (_streamGenerations.get(streamSessionId) || 0) + 1;",
        "_sendInFlight = false;",
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
      const oldCtrl = {{
        _reason: '',
        signal: {{ aborted: false }},
        abort() {{ this.signal.aborted = true; }},
      }};
      // Old send (generation 1) queues a Stop before its headers arrive.
      _streamGenerations.set('session-1', 1);
      const oldGeneration = 1;
      _stopExactRun('session-1', oldCtrl);
      const queuedBefore = _pendingRunStops.has('session-1:1');
      // Replacement send starts: bumps the generation, leaves the queued Stop.
      {{
        const streamSessionId = 'session-1';
        {resend_reset}
      }}
      // The replacement is ALSO stopped before its headers arrive: both
      // sends' cancellation intents must coexist, neither displacing the
      // other (a single session-keyed slot loses the old send's Stop, and
      // with it the only cancel for that run if this replacement dies
      // before its own POST reaches the server).
      const newCtrl = {{
        _reason: '',
        signal: {{ aborted: false }},
        abort() {{ this.signal.aborted = true; }},
      }};
      _stopExactRun('session-1', newCtrl);
      const afterResend = {{
        oldQueuedKept: _pendingRunStops.has('session-1:1'),
        newQueued: _pendingRunStops.has('session-1:2'),
        oldAborted: oldCtrl.signal.aborted,
        generation: _streamGenerations.get('session-1'),
      }};
      // The old POST's headers finally arrive: its queued Stop fires with its
      // own run id, and the old controller aborts.
      {{
        const streamSessionId = 'session-1';
        const streamGeneration = oldGeneration;
        const res = {{ headers: {{ get(name) {{
          return name === 'X-Odysseus-Run-Id' ? 'old-run' : null;
        }} }} }};
        {header_capture}
      }}
      await new Promise(resolve => setTimeout(resolve, 0));
      console.log(JSON.stringify({{
        queuedBefore,
        afterResend,
        afterOldHeaders: {{
          oldQueued: _pendingRunStops.has('session-1:1'),
          newQueuedKept: _pendingRunStops.has('session-1:2'),
          oldAborted: oldCtrl.signal.aborted,
          oldReason: oldCtrl._reason,
          newAborted: newCtrl.signal.aborted,
          currentRunIdPolluted: _streamRunIds.has('session-1'),
          stopCalls: calls.map(call => ({{
            url: call.url,
            runId: call.options.headers['X-Odysseus-Run-Id'],
          }})),
        }},
      }}));
    """

    assert _run_node(script) == {
        "queuedBefore": True,
        "afterResend": {
            "oldQueuedKept": True,
            "newQueued": True,
            "oldAborted": False,
            "generation": 2,
        },
        "afterOldHeaders": {
            "oldQueued": False,
            # The replacement's own queued Stop must survive the old send's
            # flush untouched.
            "newQueuedKept": True,
            "oldAborted": True,
            "oldReason": "user-stop",
            "newAborted": False,
            # The stale send's run id must not become the replacement's
            # identity, but its exact Stop must still go out.
            "currentRunIdPolluted": False,
            "stopCalls": [
                {"url": "/api/chat/stop/session-1", "runId": "old-run"}
            ],
        },
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_superseded_stream_cleanup_leaves_replacement_state_alone():
    """A stale send's finally must not clear state the replacement owns.

    Ownership is decided by generation, which the replacement bumps at its
    very first synchronous step — so the guard holds even in the window
    BEFORE the replacement registers its own stream entry (where the old
    finally still sees its own registration and controller identity alone
    would call it the owner).
    """

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    finally_cleanup = _extract_source(
        _CHAT,
        "const _ownsStreamState =",
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
      function runCleanup(abortCtrl, streamGeneration) {{
        const streamSessionId = 'session-1';
        const _sendState = {{ generation: streamGeneration, abortCtrl }};
        {finally_cleanup}
        return _ownsStreamState;
      }}
      const oldCtrl = {{ signal: {{ aborted: true }}, abort() {{}} }};
      const newCtrl = {{ signal: {{ aborted: false }}, abort() {{}} }};
      // Pre-registration supersession: the replacement bumped the generation
      // and set the session id, but has NOT registered its stream entry yet —
      // the old send's own entry is still the one in the map.
      _streamGenerations.set('session-1', 2);
      _streamSessionId = 'session-1';
      _activeStreams.set('session-1', {{ abortCtrl: oldCtrl, holder: null, lastActivity: 1 }});
      _pendingRunStops.set('session-1:2', newCtrl);
      const preRegOwns = runCleanup(oldCtrl, 1);
      const afterPreReg = {{
        ownEntryRemoved: !_activeStreams.has('session-1'),
        replacementPendingKept: _pendingRunStops.has('session-1:2'),
        sessionKept: _streamSessionId === 'session-1',
      }};
      // Post-registration supersession: the replacement's entry is in the map.
      _activeStreams.set('session-1', {{ abortCtrl: newCtrl, holder: null, lastActivity: 2 }});
      const postRegOwns = runCleanup(oldCtrl, 1);
      const afterPostReg = {{
        replacementRegistrationKept: _activeStreams.has('session-1'),
        replacementPendingKept: _pendingRunStops.has('session-1:2'),
        sessionKept: _streamSessionId === 'session-1',
      }};
      // Owner: the current-generation send cleans up normally.
      const ownerOwns = runCleanup(newCtrl, 2);
      const afterOwner = {{
        registered: _activeStreams.has('session-1'),
        pendingKept: _pendingRunStops.has('session-1:2'),
        sessionCleared: _streamSessionId === null,
      }};
      console.log(JSON.stringify({{
        preRegOwns, afterPreReg, postRegOwns, afterPostReg, ownerOwns, afterOwner,
      }}));
    """

    assert _run_node(script) == {
        "preRegOwns": False,
        "afterPreReg": {
            "ownEntryRemoved": True,
            "replacementPendingKept": True,
            "sessionKept": True,
        },
        "postRegOwns": False,
        "afterPostReg": {
            "replacementRegistrationKept": True,
            "replacementPendingKept": True,
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
def test_real_reservation_supersedes_and_stale_cleanup_keeps_gate_closed():
    """Drive the REAL send-commit reservation, then a stale send's cleanup.

    The reservation is synchronous, so the previous send is superseded before
    any await runs; its cleanup must then neither clear session state nor
    resync the foreground globals (a stale sync would set isStreaming false
    while _sendInFlight is already false, reopening the send gate before the
    replacement registers).
    """

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    reservation = _extract_source(
        _CHAT,
        "const streamGeneration = (_streamGenerations.get(streamSessionId) || 0) + 1;",
        "_sendInFlight = false;",
    )
    finally_cleanup = _extract_source(
        _CHAT,
        "const _ownsStreamState =",
        "// Streaming done — let screen readers announce",
    )
    script = f"""
      let currentAbort = null;
      let isStreaming = true;
      let currentHolder = null;
      let _sendInFlight = false;
      function _setForegroundChatBusy() {{}}
      const window = {{}};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      {state_and_stop}
      const oldCtrl = {{ signal: {{ aborted: false }}, abort() {{ this.signal.aborted = true; }} }};
      // Old send (generation 1) is mid-stream and registered.
      _streamGenerations.set('session-1', 1);
      _streamSessionId = 'session-1';
      _activeStreams.set('session-1', {{ abortCtrl: oldCtrl, holder: null, lastActivity: 1 }});
      // Replacement commits: run the REAL reservation block synchronously.
      let installed;
      {{
        const streamSessionId = 'session-1';
        {reservation}
        installed = {{ generation: streamGeneration, sendState: _sendState }};
      }}
      const afterReservation = {{
        generation: _streamGenerations.get('session-1'),
        sendStateInstalled: _sendStates.get('session-1') === installed.sendState,
        controllerPending: installed.sendState.abortCtrl === null,
      }};
      // Old send's cleanup runs mid-preflight (before the replacement
      // registers): it must treat itself as superseded.
      let staleOwns;
      {{
        const streamSessionId = 'session-1';
        const streamGeneration = 1;
        const abortCtrl = oldCtrl;
        const _sendState = {{ generation: 1, abortCtrl: oldCtrl }};
        {finally_cleanup}
        staleOwns = _ownsStreamState;
      }}
      console.log(JSON.stringify({{
        afterReservation,
        staleOwns,
        afterStaleCleanup: {{
          sessionKept: _streamSessionId === 'session-1',
          sendStateKept: _sendStates.get('session-1') === installed.sendState,
          gateStillClosed: isStreaming === true,
        }},
      }}));
    """

    assert _run_node(script) == {
        "afterReservation": {
            "generation": 2,
            "sendStateInstalled": True,
            "controllerPending": True,
        },
        "staleOwns": False,
        "afterStaleCleanup": {
            "sessionKept": True,
            "sendStateKept": True,
            # isStreaming untouched because the superseded send skipped the
            # foreground resync entirely.
            "gateStillClosed": True,
        },
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_stale_preflight_bails_before_creating_controller():
    """A send superseded during preflight must not proceed to register/POST."""

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    preflight_gate = _extract_source(
        _CHAT, "// Superseded during preflight", "currentAbort = abortCtrl;"
    ) + "currentAbort = abortCtrl;"
    script = f"""
      let currentAbort = null;
      let isStreaming = false;
      let currentHolder = null;
      let _sendInFlight = false;
      function _setForegroundChatBusy() {{}}
      const window = {{}};
      const document = {{
        createElement() {{ return {{ style: {{}}, textContent: '' }}; }},
      }};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      {state_and_stop}
      function runPreflightGate(streamGeneration, _sendState, _userMsgEl) {{
        const streamSessionId = 'session-1';
        let abortCtrl = null;
        {preflight_gate}
        return abortCtrl;
      }}
      // Stale: generation 1 resumes after generation 2 reserved the session.
      // Its optimistic user bubble must be marked undelivered, not left as a
      // ghost that looks sent.
      _streamGenerations.set('session-1', 2);
      const staleState = {{ generation: 1, abortCtrl: null }};
      const staleBubble = {{
        parentNode: {{}},
        notes: [],
        appendChild(node) {{ this.notes.push(node.textContent); }},
      }};
      const staleResult = runPreflightGate(1, staleState, staleBubble);
      // Current: generation 2 proceeds and wires its controller.
      const currentState = {{ generation: 2, abortCtrl: null }};
      const currentBubble = {{
        parentNode: {{}},
        notes: [],
        appendChild(node) {{ this.notes.push(node.textContent); }},
      }};
      const currentResult = runPreflightGate(2, currentState, currentBubble);
      console.log(JSON.stringify({{
        staleBailed: staleResult === undefined,
        staleControllerNever: staleState.abortCtrl === null,
        staleBubbleNotes: staleBubble.notes,
        currentProceeded: !!currentResult,
        currentWired: currentState.abortCtrl === currentResult && currentAbort === currentResult,
        currentBubbleNotes: currentBubble.notes,
      }}));
    """

    assert _run_node(script) == {
        "staleBailed": True,
        "staleControllerNever": True,
        "staleBubbleNotes": ["[Not sent — superseded by a newer message]"],
        "currentProceeded": True,
        "currentWired": True,
        "currentBubbleNotes": [],
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_stop_during_replacement_preflight_never_borrows_old_controller():
    """Stop must take the current send's controller from its send state.

    During the replacement's preflight the stream registry still holds the
    superseded send's entry; borrowing that controller would abort the only
    identity channel able to name the old run while queueing a Stop for the
    new one. A committed-but-pre-POST send has a null controller: the Stop
    queues under the new generation and nothing is aborted yet.
    """

    state_and_stop = _extract_source(
        _CHAT, "const _backgroundStreams", "// Sources box builder"
    )
    abort_current = _extract_source(
        _CHAT, "export function abortCurrentRequest", "// ── Stall watchdog"
    ).replace("export function", "function")
    reservation = _extract_source(
        _CHAT,
        "const streamGeneration = (_streamGenerations.get(streamSessionId) || 0) + 1;",
        "_sendInFlight = false;",
    )
    script = f"""
      let currentAbort = null;
      let isStreaming = false;
      let currentHolder = null;
      let _sendInFlight = false;
      function _setForegroundChatBusy() {{}}
      const calls = [];
      const fetch = async (url, options) => {{ calls.push({{ url, options }}); return {{ ok: true }}; }};
      const window = {{}};
      const sessionModule = {{ getCurrentSessionId() {{ return 'session-1'; }} }};
      {state_and_stop}
      {abort_current}
      const oldCtrl = {{ signal: {{ aborted: false }}, abort() {{ this.signal.aborted = true; }} }};
      // Generation 1 is registered, streaming, and its run id is KNOWN — the
      // exact window daybreak probed: a Stop right after the replacement
      // commits must not consume the old run identity.
      _streamGenerations.set('session-1', 1);
      _streamRunIds.set('session-1', 'old-run');
      _activeStreams.set('session-1', {{ abortCtrl: oldCtrl, holder: null, lastActivity: 1 }});
      currentAbort = oldCtrl;
      // Replacement (generation 2) commits via the REAL reservation block; no
      // controller exists yet and the model-switch await has not resolved.
      {{
        const streamSessionId = 'session-1';
        {reservation}
      }}
      abortCurrentRequest(true);
      const preRegistration = {{
        oldRunIdCleared: !_streamRunIds.has('session-1'),
        queuedForNew: _pendingRunStops.has('session-1:2'),
        queuedController: _pendingRunStops.get('session-1:2') || null,
        oldAborted: oldCtrl.signal.aborted,
        stopCalls: calls.length,
      }};
      // Normal case: the current send's own controller, run id known.
      const ownCtrl = {{ _reason: '', signal: {{ aborted: false }}, abort() {{ this.signal.aborted = true; }} }};
      _sendStates.set('session-1', {{ generation: 2, abortCtrl: ownCtrl }});
      _streamRunIds.set('session-1', 'run-2');
      abortCurrentRequest(true);
      await new Promise(resolve => setTimeout(resolve, 0));
      console.log(JSON.stringify({{
        preRegistration,
        normal: {{
          ownAborted: ownCtrl.signal.aborted,
          oldStillUntouched: oldCtrl.signal.aborted,
          stopRunId: calls[0] && calls[0].options.headers['X-Odysseus-Run-Id'],
        }},
      }}));
    """

    assert _run_node(script) == {
        "preRegistration": {
            # The old run identity dies at reservation: the Stop queues for
            # the NEW send instead of firing against the old run and skipping
            # the queue entirely.
            "oldRunIdCleared": True,
            "queuedForNew": True,
            "queuedController": None,
            "oldAborted": False,
            "stopCalls": 0,
        },
        "normal": {
            "ownAborted": True,
            "oldStillUntouched": False,
            "stopRunId": "run-2",
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
      const streamGeneration = 1;
      _streamGenerations.set(streamSessionId, streamGeneration);
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
