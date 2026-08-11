import json
from pathlib import Path
import shutil
import subprocess

import pytest


_REPO = Path(__file__).resolve().parents[1]
_EMAIL_LIBRARY = _REPO / "static" / "js" / "emailLibrary.js"


def _source() -> str:
    return _EMAIL_LIBRARY.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    """Return one top-level JS function using balanced braces."""
    text = _source()
    markers = (f"function {name}", f"async function {name}", f"export function {name}", f"export async function {name}")
    starts = [text.find(marker) for marker in markers]
    starts = [start for start in starts if start >= 0]
    assert starts, f"missing function {name}"
    start = min(starts)
    paren = text.index("(", start)
    paren_depth = 0
    quote = None
    escaped = False
    for index in range(paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
            if paren_depth == 0:
                brace = text.index("{", index)
                break
    else:
        raise AssertionError(f"unterminated signature {name}")
    depth = 0
    quote = None
    escaped = False
    template_depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote and template_depth == 0:
                quote = None
            elif quote == "`" and char == "$" and index + 1 < len(text) and text[index + 1] == "{":
                template_depth += 1
            elif quote == "`" and char == "}" and template_depth:
                template_depth -= 1
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError(f"unterminated function {name}")


def _run_scheduler_scenario(scenario: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    functions = "\n".join(
        _function_source(name)
        for name in (
            "_isChatInteractionBusy",
            "_canRunEmailPrewarm",
            "_isEmailPrewarmTemporarilyBlocked",
            "_settleEmailPrewarm",
            "_cancelEmailPrewarm",
            "_scheduleEmailPrewarm",
        )
    )
    script = f"""
      let now = 0;
      Date.now = () => now;
      const state = {{ _libOpen: false, _libLoading: false }};
      let _libSearchInFlight = false;
      let _libPrewarmDelayTimer = null;
      let _libPrewarmIdleHandle = null;
      let _libPrewarmPromise = null;
      let _libPrewarmResolve = null;
      let _libPrewarmAbortController = null;
      let _libPrewarmDetachPriorityListeners = null;
      let _libPrewarmGeneration = 0;
      let nextHandle = 1;
      const timers = new Map();
      const idleCallbacks = new Map();
      let idleRequestCount = 0;
      function eventTarget(target) {{
        const listeners = new Map();
        target.addEventListener = (type, callback) => {{
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(callback);
        }};
        target.removeEventListener = (type, callback) => listeners.get(type)?.delete(callback);
        target.dispatchEvent = (event) => {{
          for (const callback of [...(listeners.get(event.type) || [])]) callback(event);
        }};
        target.listenerCount = (type) => listeners.get(type)?.size || 0;
        return target;
      }}
      const document = eventTarget({{ visibilityState: 'visible' }});
      const window = {{
        __odysseusChatBusy: false,
        __odysseusChatBusyUntil: 0,
        requestIdleCallback(callback) {{
          const handle = nextHandle++;
          idleRequestCount += 1;
          idleCallbacks.set(handle, callback);
          return handle;
        }},
        cancelIdleCallback(handle) {{ idleCallbacks.delete(handle); }},
      }};
      eventTarget(window);
      function setTimeout(callback, delay) {{
        const handle = nextHandle++;
        timers.set(handle, {{ callback, at: now + Number(delay || 0) }});
        return handle;
      }}
      function clearTimeout(handle) {{ timers.delete(handle); }}
      async function flushMicrotasks() {{
        for (let i = 0; i < 6; i += 1) await Promise.resolve();
      }}
      async function advanceTo(target) {{
        while (true) {{
          const pending = [...timers.entries()]
            .filter(([, timer]) => timer.at <= target)
            .sort((a, b) => a[1].at - b[1].at)[0];
          if (!pending) break;
          const [handle, timer] = pending;
          timers.delete(handle);
          now = timer.at;
          timer.callback();
          await flushMicrotasks();
        }}
        now = target;
        await flushMicrotasks();
      }}
      async function fireNextIdle(budget = 5) {{
        const pending = idleCallbacks.entries().next().value;
        if (!pending) throw new Error('no idle callback pending');
        const [handle, callback] = pending;
        idleCallbacks.delete(handle);
        callback({{ didTimeout: false, timeRemaining: () => budget }});
        await flushMicrotasks();
      }}
      {functions}
      {scenario}
    """
    proc = subprocess.run(
        [node, "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_prewarm_is_genuine_idle_only_and_single_flight():
    scheduler = _function_source("_scheduleEmailPrewarm")

    assert "if (_libPrewarmPromise) return _libPrewarmPromise;" in scheduler
    assert "typeof window.requestIdleCallback !== 'function'" in scheduler
    assert "return Promise.resolve(false);" in scheduler
    assert "window.requestIdleCallback((deadline)" in scheduler
    assert "!deadline.didTimeout" in scheduler
    assert "deadline.timeRemaining() > 0" in scheduler

    idle_callback = scheduler.index("window.requestIdleCallback((deadline)")
    assert "Promise.resolve()" in scheduler
    task_start = scheduler.index("task({ signal: controller.signal, generation })")
    assert idle_callback < task_start, "network work must only be reachable from the idle callback"


def test_temporary_chat_priority_retries_one_single_flight_until_idle():
    out = _run_scheduler_scenario("""
      window.__odysseusChatBusyUntil = 10000;
      let taskCalls = 0;
      const task = async () => { taskCalls += 1; return true; };
      const first = _scheduleEmailPrewarm(task, { delay: 1800 });
      const joined = _scheduleEmailPrewarm(task, { delay: 0 });
      const samePromise = first === joined;
      await advanceTo(1800);
      await fireNextIdle(7);
      const callsWhileBusy = taskCalls;
      while (now < 10300) {
        await advanceTo(now + 500);
        await fireNextIdle(7);
      }
      const result = await first;
      console.log(JSON.stringify({
        result, samePromise, callsWhileBusy, taskCalls, idleRequestCount,
        timers: timers.size, idleCallbacks: idleCallbacks.size,
      }));
    """)
    assert out == {
        "result": True,
        "samePromise": True,
        "callsWhileBusy": 0,
        "taskCalls": 1,
        "idleRequestCount": 18,
        "timers": 0,
        "idleCallbacks": 0,
    }


def test_cancelled_prewarm_cannot_issue_a_delayed_duplicate():
    out = _run_scheduler_scenario("""
      let taskCalls = 0;
      const pending = _scheduleEmailPrewarm(async () => { taskCalls += 1; return true; }, { delay: 1800 });
      await advanceTo(1400);
      _cancelEmailPrewarm();
      await advanceTo(12000);
      const result = await pending;
      console.log(JSON.stringify({
        result, taskCalls, idleRequestCount,
        timers: timers.size, idleCallbacks: idleCallbacks.size,
      }));
    """)
    assert out == {
        "result": False,
        "taskCalls": 0,
        "idleRequestCount": 0,
        "timers": 0,
        "idleCallbacks": 0,
    }


@pytest.mark.parametrize("transition", ["busy", "hidden"])
def test_active_prewarm_is_aborted_and_retried_once_after_priority_transition(transition):
    block = (
        "window.__odysseusChatBusy = true; "
        "window.dispatchEvent({ type: 'odysseus:chat-busy-change' });"
        if transition == "busy"
        else "document.visibilityState = 'hidden'; document.dispatchEvent({ type: 'visibilitychange' });"
    )
    unblock = (
        "window.__odysseusChatBusy = false; window.__odysseusChatBusyUntil = now; "
        "window.dispatchEvent({ type: 'odysseus:chat-busy-change' });"
        if transition == "busy"
        else "document.visibilityState = 'visible'; document.dispatchEvent({ type: 'visibilitychange' });"
    )
    out = _run_scheduler_scenario(f"""
      let taskCalls = 0;
      let firstSignal = null;
      let finishFirst;
      const firstAttempt = new Promise(resolve => {{ finishFirst = resolve; }});
      const pending = _scheduleEmailPrewarm(async ({{ signal }}) => {{
        taskCalls += 1;
        if (taskCalls === 1) {{ firstSignal = signal; return firstAttempt; }}
        return true;
      }});
      await fireNextIdle(7);
      {block}
      const aborted = firstSignal.aborted;
      {unblock}
      const callsBeforeLateResult = taskCalls;
      finishFirst(true);
      await flushMicrotasks();
      const stillPendingAfterLateResult = _libPrewarmPromise === pending;
      await advanceTo(now + 500);
      await fireNextIdle(7);
      const result = await pending;
      console.log(JSON.stringify({{
        result, aborted, callsBeforeLateResult, taskCalls,
        stillPendingAfterLateResult,
        timers: timers.size, idleCallbacks: idleCallbacks.size,
        chatListeners: window.listenerCount('odysseus:chat-busy-change'),
        visibilityListeners: document.listenerCount('visibilitychange'),
      }}));
    """)
    assert out == {
        "result": True,
        "aborted": True,
        "callsBeforeLateResult": 1,
        "taskCalls": 2,
        "stillPendingAfterLateResult": True,
        "timers": 0,
        "idleCallbacks": 0,
        "chatListeners": 0,
        "visibilityListeners": 0,
    }


def test_prewarm_skips_hidden_and_foreground_work():
    guard = _function_source("_canRunEmailPrewarm")

    assert "state._libOpen" in guard
    assert "state._libLoading" in guard
    assert "_libSearchInFlight" in guard
    assert "document.visibilityState !== 'visible'" in guard
    assert "!_isChatInteractionBusy()" in guard


def test_prewarm_selects_only_last_used_or_default_account():
    chooser = _function_source("_chooseEmailPrewarmAccountId")
    prewarm = _function_source("_prewarmEmailViews")

    assert "_rememberedEmailAccountId()" in chooser
    assert "a.enabled !== false" in chooser
    assert "a.is_default" in chooser
    assert "enabled[0]" in chooser

    assert "for (" not in prewarm
    assert "orderedAccountIds" not in prewarm
    assert "slice(0, 4)" not in prewarm
    assert "/api/email/folders" not in prewarm
    assert "/api/email/unread-state" not in prewarm
    assert prewarm.count("/api/email/list") == 1


def test_prewarm_account_chooser_rejects_disabled_or_empty_authoritative_inventory():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    chooser = _function_source("_chooseEmailPrewarmAccountId")
    script = f"""
      const state = {{ _libAccountId: 'disabled-current' }};
      function _rememberedEmailAccountId() {{ return 'disabled-remembered'; }}
      {chooser}
      const onlyDisabled = _chooseEmailPrewarmAccountId([
        {{ id: 'disabled-remembered', enabled: false, is_default: true }},
        {{ id: 'disabled-current', enabled: false }},
      ]);
      const empty = _chooseEmailPrewarmAccountId([]);
      const mixed = _chooseEmailPrewarmAccountId([
        {{ id: 'disabled-remembered', enabled: false, is_default: true }},
        {{ id: 'enabled-default', enabled: true, is_default: true }},
      ]);
      console.log(JSON.stringify({{ onlyDisabled, empty, mixed }}));
    """
    proc = subprocess.run(
        [node, "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {
        "onlyDisabled": "",
        "empty": "",
        "mixed": "enabled-default",
    }

    ensure_accounts = _function_source("_ensureEmailAccountsForPrewarm")
    assert "if (!accountId) return null;" in ensure_accounts
    assert ensure_accounts.index("if (!accountId) return null;") < ensure_accounts.index("_publishActiveAccount();")


def test_prewarm_is_bounded_to_the_interactive_initial_page_size():
    text = _source()
    prewarm = _function_source("_prewarmEmailViews")

    assert "const _LIB_INITIAL_PAGE_SIZE = 100;" in text
    assert "limit: _LIB_INITIAL_PAGE_SIZE" in prewarm
    assert text.count("limit=${_LIB_INITIAL_PAGE_SIZE}&offset=${offsetAtStart}") == 2
    assert "limit: 100" not in prewarm


def test_open_cancels_scheduled_or_inflight_prewarm_first():
    text = _source()
    cancel = _function_source("_cancelEmailPrewarm")
    open_library = _function_source("openEmailLibrary")

    assert "clearTimeout(_libPrewarmDelayTimer)" in cancel
    assert "window.cancelIdleCallback(_libPrewarmIdleHandle)" in cancel
    assert "_libPrewarmAbortController?.abort()" in cancel
    assert "_libPrewarmGeneration += 1" in cancel
    assert open_library.index("_cancelEmailPrewarm();") < open_library.index("state._libOpen = true;")
    assert "_loadEmailsWhenChatIdle" not in text
    assert text.count("_loadEmails({ useCache: true });") >= 2


def test_close_cancels_pending_prewarm_cleanup():
    close_library = _function_source("closeEmailLibrary")

    assert close_library.index("_cancelEmailPrewarm();") < close_library.index("state._libOpen = false;")


def test_unread_warm_joins_the_same_idle_single_flight_gate():
    unread_entry = _function_source("prewarmUnreadEmails")
    unread_work = _function_source("_prewarmUnreadEmailsNow")

    assert "_scheduleEmailPrewarm(" in unread_entry
    assert "fetch(" not in unread_entry
    assert "_ensureEmailAccountsForPrewarm({ signal, generation })" in unread_work
    assert "signal" in unread_work
    assert "Math.min(20" in unread_work
