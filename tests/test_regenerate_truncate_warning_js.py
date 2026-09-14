"""Executable regression for the "regenerate" data-loss warning.

Both `regenerateFrom` (the per-message ↻ footer button) and
`resendUserMessage(..., { replaceFromHere: true })` (the vision editor's
"Regenerate message" button) permanently delete every message after the
point clicked via POST /api/session/{id}/truncate — a real, unrecoverable
server-side delete. Clicking either on anything but the very last exchange
used to do this with no warning at all; a mis-click could silently erase an
entire conversation. This drives the real chat.js functions under Node,
confirming:

  1. Regenerating the *last* exchange (nothing after it) never prompts and
     truncates directly — the everyday case must not gain friction.
  2. Regenerating anything earlier prompts first, with the correct count of
     messages that would be destroyed in the message text.
  3. Cancelling the prompt aborts before any network call — nothing is
     deleted.
  4. Confirming the prompt proceeds with the correct `keep_count`.

Both entry points (`regenerateFrom` and `resendUserMessage`) are exercised
identically since they wrap the same destructive operation.

Unlike test_pr6020_browser_review_regressions.py's `_chat_smoke_source`
(which appends extra code to a copy of chat.js's own source — fine for
defining an extra exported function, since ES module static imports are
hoisted and evaluated before any top-level code regardless of where in the
file it appears), this harness needs its global stubs (document, fetch,
etc.) in place *before* chat.js's own import tree evaluates, since some of
those transitive dependencies touch browser globals at their own module top
level. So this loads chat.js via a real dynamic `import()` of its actual
file, in a small wrapper script that sets the stubs up first as plain
synchronous statements — dynamic `import()` runs in program order, unlike a
static `import` declaration.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_JS_DIR = _REPO / "static" / "js"
_HAS_NODE = shutil.which("node") is not None


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
    return json.loads(proc.stdout.strip().splitlines()[-1])


_HARNESS_PREAMBLE = """
      globalThis.window = globalThis;
      Object.defineProperty(globalThis, "navigator", { value: { platform: "" }, writable: true, configurable: true });
      globalThis.addEventListener = () => {};
      globalThis.removeEventListener = () => {};
      globalThis.dispatchEvent = () => {};
      globalThis.requestAnimationFrame = () => 0;
      globalThis.cancelAnimationFrame = () => {};
      globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
      globalThis.location = {};
      globalThis.history = {};
      globalThis.MutationObserver = class { observe() {} };
      globalThis.CustomEvent = class {};
      globalThis.Storage = class {};

      const fetchCalls = [];
      globalThis.fetch = async (url, opts) => {
        fetchCalls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
        return { ok: true, json: async () => ({}), text: async () => '', headers: { get() { return null; } } };
      };

      class Element {
        constructor() {
          this.children = [];
          this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
          this.style = { setProperty() {} };
          this.dataset = {};
        }
        querySelector() { return null; }
        querySelectorAll() { return []; }
        appendChild(child) { this.children.push(child); return child; }
        addEventListener() {}
        removeEventListener() {}
      }
      class HTMLInputElement extends Element {
        get value() { return this._value || ''; }
        set value(v) { this._value = v; }
      }
      globalThis.HTMLInputElement = HTMLInputElement;

      // A minimal .msg-list DOM: `msgs` is an ordered array of
      // { role: 'user'|'ai' } describing one conversation. Each entry
      // becomes a fake message element good enough for regenerateFrom /
      // resendUserMessage's own traversal (classList.contains, .dataset,
      // querySelector('.body'), querySelectorAll('[data-file-id]')).
      function buildMsgEls(msgs) {
        return msgs.map((m, i) => ({
          classList: { contains: (c) => c === (m === 'user' ? 'msg-user' : 'msg-ai') },
          dataset: { raw: m === 'user' ? `question ${i}` : '' },
          querySelector: (sel) => sel === '.body' ? { textContent: m === 'user' ? `question ${i}` : `answer ${i}`, innerHTML: `answer ${i}` } : null,
          querySelectorAll: () => [],
          nextSibling: null,
          remove() {},
        }));
      }

      const sendClicks = [];
      const messageInputEl = new HTMLInputElement();
      const sendBtnEl = { click: () => sendClicks.push(messageInputEl.value) };

      globalThis.document = {
        body: new Element(),
        head: new Element(),
        documentElement: new Element(),
        getElementById(id) {
          if (id === 'message') return messageInputEl;
          if (id === 'chat-history') return this._box;
          return null;
        },
        querySelector(sel) { return sel === '.send-btn' ? sendBtnEl : null; },
        querySelectorAll() { return []; },
        createElement(tag) { return tag === 'input' ? new HTMLInputElement() : new Element(); },
        createTextNode(text) { return { textContent: text }; },
        addEventListener() {},
        removeEventListener() {},
      };
"""


def _scenario_script(func_call_js: str, roles: list, target_index: int, confirm_resolution: bool) -> str:
    # Globals are stubbed above as plain synchronous statements *before* the
    # dynamic import()s below run — unlike a static `import` declaration,
    # dynamic import() executes in program order, so chat.js's transitive
    # dependency tree only evaluates once the stubs are already in place.
    script = _HARNESS_PREAMBLE
    script += f"""
      const chat = await import({json.dumps((_JS_DIR / "chat.js").resolve().as_uri())});
      const {{ default: uiMod }} = await import({json.dumps((_JS_DIR / "ui.js").resolve().as_uri())});
      const {{ default: sessionMod }} = await import({json.dumps((_JS_DIR / "sessions.js").resolve().as_uri())});

      sessionMod.getCurrentSessionId = () => 'test-session';

      const confirmCalls = [];
      uiMod.styledConfirm = async (message, opts) => {{
        confirmCalls.push({{ message, opts }});
        return {json.dumps(confirm_resolution)};
      }};
      uiMod.showError = () => {{}};
      uiMod.showToast = () => {{}};

      const msgs = buildMsgEls({json.dumps(roles)});
      const box = {{ querySelectorAll: () => msgs, _isBox: true }};
      globalThis.document._box = box;
      const target = msgs[{target_index}];

      await {func_call_js};

      console.log(JSON.stringify({{
        confirmShown: confirmCalls.length > 0,
        confirmMessage: confirmCalls.length ? confirmCalls[0].message : null,
        truncateCalled: fetchCalls.some(c => c.url.includes('/truncate')),
        truncateKeepCount: (fetchCalls.find(c => c.url.includes('/truncate')) || {{}}).body?.keep_count ?? null,
      }}));
      process.exit(0);
    """
    return script


# ---------------------------------------------------------------------------
# regenerateFrom (main chat footer "↻ Regenerate from here")
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regenerate_from_last_message_skips_prompt_and_truncates():
    roles = ["user", "ai"]  # target (index 1) IS the last message
    script = _scenario_script("chat.regenerateFrom(target)", roles, 1, confirm_resolution=True)
    result = _run_node(script)
    assert result["confirmShown"] is False
    assert result["truncateCalled"] is True
    assert result["truncateKeepCount"] == 0  # keep everything before the user turn (index 0)


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regenerate_from_earlier_message_prompts_with_correct_count():
    roles = ["user", "ai", "user", "ai", "user", "ai"]  # target (index 1) has 4 messages after it
    script = _scenario_script("chat.regenerateFrom(target)", roles, 1, confirm_resolution=True)
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert "4 messages" in result["confirmMessage"]
    assert result["truncateCalled"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regenerate_from_earlier_message_cancel_aborts_without_truncating():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.regenerateFrom(target)", roles, 1, confirm_resolution=False)
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert result["truncateCalled"] is False


# ---------------------------------------------------------------------------
# resendUserMessage(..., { replaceFromHere: true }) (vision editor "Regenerate message")
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_replace_from_here_last_pair_skips_prompt():
    roles = ["user", "ai"]  # target (index 0) is the user turn of the last pair
    script = _scenario_script(
        "chat.resendUserMessage(target, { replaceFromHere: true })", roles, 0, confirm_resolution=True,
    )
    result = _run_node(script)
    assert result["confirmShown"] is False
    assert result["truncateCalled"] is True
    assert result["truncateKeepCount"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_replace_from_here_earlier_pair_prompts_with_correct_count():
    roles = ["user", "ai", "user", "ai", "user", "ai"]  # target (index 0) has 4 messages after its own pair
    script = _scenario_script(
        "chat.resendUserMessage(target, { replaceFromHere: true })", roles, 0, confirm_resolution=True,
    )
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert "4 messages" in result["confirmMessage"]
    assert result["truncateCalled"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_replace_from_here_cancel_aborts_without_truncating():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script(
        "chat.resendUserMessage(target, { replaceFromHere: true })", roles, 0, confirm_resolution=False,
    )
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert result["truncateCalled"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_plain_never_truncates_or_prompts():
    """Plain resend (no replaceFromHere) must stay non-destructive."""
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.resendUserMessage(target, {})", roles, 0, confirm_resolution=True)
    result = _run_node(script)
    assert result["confirmShown"] is False
    assert result["truncateCalled"] is False
