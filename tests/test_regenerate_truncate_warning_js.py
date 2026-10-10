"""Executable regression for the "regenerate" data-loss warning.

`regenerateFrom` (the per-message ↻ footer button), `resendUserMessage`
(footer Resend and the vision editor's "Regenerate message" button) and
`editUserMessage` (sending an edited user message) all permanently delete
every message after the point clicked via POST /api/session/{id}/truncate —
a real, unrecoverable server-side delete. Using any of them on anything but
the very last exchange used to do this with no warning at all; a mis-click
could silently erase an entire conversation. This drives the real chat.js
functions under Node, confirming:

  1. Regenerating the *last* exchange (nothing after it) never prompts and
     truncates directly — the everyday case must not gain friction.
  2. Regenerating anything earlier prompts first, with the correct count of
     messages that would be destroyed in the message text.
  3. Cancelling the prompt aborts before any network call — nothing is
     deleted.
  4. Confirming the prompt proceeds with the correct `keep_count`.
  5. Choosing "Fork from here" deletes nothing: it forks the chat just
     before the user turn and replays the action (same text, or the edited
     text) in the new chat instead.

All three entry points are exercised the same way since they wrap the same
destructive operation.

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
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_JS_DIR = _REPO / "static" / "js"
_HAS_NODE = shutil.which("node") is not None


def _versioned_js_uri(entry_js: Path, target_name: str) -> str:
    """file:// URI for target_name with the same cache-busting query string
    entry_js's own import of it uses. ES modules cache by full URL, so
    importing 'ui.js' here while chat.js imports 'ui.js?v=X' yields two module
    instances, and patching uiMod here would never reach chat.js."""
    src = entry_js.read_text(encoding="utf-8")
    m = re.search(rf"from ['\"]\./{re.escape(target_name)}(\?[^'\"]*)?['\"]", src)
    suffix = (m.group(1) or "") if m else ""
    return (entry_js.parent / target_name).resolve().as_uri() + suffix


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
        const data = url.includes('/fork') ? { id: 'forked-session', name: 'Forked chat' } : {};
        // A test can make the server refuse the cut (e.g. a stale message id).
        const ok = !(url.includes('/truncate') && globalThis.__refuseTruncate);
        return { ok, status: ok ? 200 : 404, json: async () => data, text: async () => '', headers: { get() { return null; } } };
      };

      class Element {
        constructor() {
          this.children = [];
          this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
          this.style = { setProperty() {} };
          this.dataset = {};
          this._listeners = {};
          this._html = '';
        }
        get innerHTML() { return this._html; }
        set innerHTML(v) { this._html = v; this.children = []; }
        querySelector() { return null; }
        querySelectorAll() { return []; }
        appendChild(child) { this.children.push(child); return child; }
        addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
        removeEventListener() {}
        focus() {}
        // Fire a listener and wait for it — the edit Send handler is async.
        fire(type) {
          const ev = { stopPropagation() {}, preventDefault() {} };
          return Promise.all((this._listeners[type] || []).map(fn => fn(ev)));
        }
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
        return msgs.map((m, i) => {
          const body = new Element();
          body.textContent = m === 'user' ? `question ${i}` : `answer ${i}`;
          body.innerHTML = body.textContent;
          return {
            classList: { contains: (c) => c === (m === 'user' ? 'msg-user' : 'msg-ai') },
            dataset: { raw: m === 'user' ? `question ${i}` : '' },
            querySelector: (sel) => sel === '.body' ? body : null,
            querySelectorAll: () => [],
            nextSibling: null,
            remove() {},
          };
        });
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


def _scenario_script(func_call_js: str, roles: list, target_index: int, confirm_resolution, then_js: str = "", db_ids: bool = False, refuse_truncate: bool = False) -> str:
    # Globals are stubbed above as plain synchronous statements *before* the
    # dynamic import()s below run — unlike a static `import` declaration,
    # dynamic import() executes in program order, so chat.js's transitive
    # dependency tree only evaluates once the stubs are already in place.
    script = _HARNESS_PREAMBLE
    script += f"""
      const chat = await import({json.dumps((_JS_DIR / "chat.js").resolve().as_uri())});
      const {{ default: uiMod }} = await import({json.dumps(_versioned_js_uri(_JS_DIR / "chat.js", "ui.js"))});
      const {{ default: sessionMod }} = await import({json.dumps(_versioned_js_uri(_JS_DIR / "chat.js", "sessions.js"))});

      let currentSession = 'test-session';
      const selectedSessions = [];
      sessionMod.getCurrentSessionId = () => currentSession;
      sessionMod.loadSessions = async () => {{}};
      sessionMod.selectSession = async (id) => {{ selectedSessions.push(id); currentSession = id; }};

      const confirmCalls = [];
      uiMod.styledConfirm = async (message, opts) => {{
        confirmCalls.push({{ message, opts }});
        return {json.dumps(confirm_resolution)};
      }};
      uiMod.showError = () => {{}};
      uiMod.showToast = () => {{}};

      globalThis.__refuseTruncate = {json.dumps(refuse_truncate)};
      const msgs = buildMsgEls({json.dumps(roles)});
      // Persisted messages carry their database id; the page may have loaded
      // only the newest of them, so ids (not positions) identify a message.
      if ({json.dumps(db_ids)}) msgs.forEach((m, i) => {{ m.dataset.dbId = `db-${{i + 40}}`; }});
      const box = {{ querySelectorAll: () => msgs, _isBox: true }};
      globalThis.document._box = box;
      const target = msgs[{target_index}];

      await {func_call_js};
      {then_js}

      const forkCall = fetchCalls.find(c => c.url.includes('/fork'));
      console.log(JSON.stringify({{
        confirmShown: confirmCalls.length > 0,
        confirmMessage: confirmCalls.length ? confirmCalls[0].message : null,
        confirmOpts: confirmCalls.length ? confirmCalls[0].opts : null,
        truncateCalled: fetchCalls.some(c => c.url.includes('/truncate')),
        truncateKeepCount: (fetchCalls.find(c => c.url.includes('/truncate')) || {{}}).body?.keep_count ?? null,
        forkCalled: Boolean(forkCall),
        forkUrl: forkCall ? forkCall.url : null,
        forkKeepCount: forkCall ? forkCall.body.keep_count : null,
        forkBody: forkCall ? forkCall.body : null,
        truncateBody: (fetchCalls.find(c => c.url.includes('/truncate')) || {{}}).body || null,
        selectedSessions,
        sendClicks,
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


# ---------------------------------------------------------------------------
# resendUserMessage(el) with no options (footer "Resend message")
# ---------------------------------------------------------------------------
# `dev` made plain resend replace-by-default (see
# test_resend_message_nondestructive.py): no options means replaceFromHere,
# so the footer Resend button now truncates everything after the clicked
# turn too, and gets the same warning. Append-only resend must be explicit.

@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_default_earlier_pair_prompts_with_correct_count():
    roles = ["user", "ai", "user", "ai", "user", "ai"]
    script = _scenario_script("chat.resendUserMessage(target)", roles, 0, confirm_resolution=True)
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert "4 messages" in result["confirmMessage"]
    assert result["truncateCalled"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_append_only_never_truncates_or_prompts():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.resendUserMessage(target, { append: true })", roles, 0, confirm_resolution=True)
    result = _run_node(script)
    assert result["confirmShown"] is False
    assert result["truncateCalled"] is False


# ---------------------------------------------------------------------------
# editUserMessage (✎ edit, then Send)
# ---------------------------------------------------------------------------
# The edit Send handler is wired up inside editUserMessage, so these open the
# editor, type into it, and click the editor's own Send button.

_EDIT_AND_SEND = """
      const _body = target.querySelector('.body');
      const [_editor, _btnRow] = _body.children;
      _editor.value = 'edited question';
      await _btnRow.children[0].fire('click');
"""


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_edit_last_message_skips_prompt_and_truncates():
    roles = ["user", "ai"]
    script = _scenario_script("chat.editUserMessage(target)", roles, 0, True, _EDIT_AND_SEND)
    result = _run_node(script)
    assert result["confirmShown"] is False
    assert result["truncateCalled"] is True
    assert result["truncateKeepCount"] == 0
    assert result["sendClicks"] == ["edited question"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_edit_earlier_message_prompts_with_correct_count():
    roles = ["user", "ai", "user", "ai", "user", "ai"]  # target (index 2) has 2 messages after its own pair
    script = _scenario_script("chat.editUserMessage(target)", roles, 2, True, _EDIT_AND_SEND)
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert "2 messages" in result["confirmMessage"]
    assert result["confirmOpts"]["confirmText"] == "Delete and send"
    assert result["truncateCalled"] is True
    assert result["truncateKeepCount"] == 2
    assert result["sendClicks"] == ["edited question"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_edit_earlier_message_cancel_aborts_without_truncating_or_sending():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.editUserMessage(target)", roles, 0, False, _EDIT_AND_SEND)
    result = _run_node(script)
    assert result["confirmShown"] is True
    assert result["truncateCalled"] is False
    assert result["sendClicks"] == []


# ---------------------------------------------------------------------------
# "Fork from here" — the prompt's alternate choice
# ---------------------------------------------------------------------------
# styledConfirm resolves to 'alternate' for its third button. Forking must
# not truncate anything; it forks just before the user turn (so the new chat
# ends where the action starts), switches to the fork, and replays there.

def _assert_forked_instead(result, keep_count, sent_text):
    assert result["confirmShown"] is True
    assert result["confirmOpts"]["alternateText"] == "Fork from here"
    assert result["truncateCalled"] is False
    assert result["forkCalled"] is True
    assert "/api/session/test-session/fork" in result["forkUrl"]
    assert result["forkKeepCount"] == keep_count
    assert result["selectedSessions"] == ["forked-session"]
    assert result["sendClicks"] == [sent_text]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regenerate_fork_choice_forks_before_user_turn_and_resends():
    roles = ["user", "ai", "user", "ai", "user", "ai"]
    script = _scenario_script("chat.regenerateFrom(target)", roles, 3, "alternate")
    _assert_forked_instead(_run_node(script), keep_count=2, sent_text="question 2")


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_fork_choice_forks_before_user_turn_and_resends():
    roles = ["user", "ai", "user", "ai", "user", "ai"]
    script = _scenario_script("chat.resendUserMessage(target)", roles, 2, "alternate")
    _assert_forked_instead(_run_node(script), keep_count=2, sent_text="question 2")


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_edit_fork_choice_forks_before_user_turn_and_sends_edit():
    roles = ["user", "ai", "user", "ai", "user", "ai"]
    script = _scenario_script("chat.editUserMessage(target)", roles, 2, "alternate", _EDIT_AND_SEND)
    _assert_forked_instead(_run_node(script), keep_count=2, sent_text="edited question")


# ---------------------------------------------------------------------------
# Cutting by message id
# ---------------------------------------------------------------------------
# A long chat loads only its newest messages until the user scrolls up, so a
# position counted in the page is smaller than the real one. Truncating by
# that position deleted real messages above the clicked one. Every action
# sends the clicked message's database id when it has one.

@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regenerate_truncates_before_the_user_message_by_id():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.regenerateFrom(target)", roles, 3, True, db_ids=True)
    result = _run_node(script)
    assert result["truncateBody"] == {"before_msg_id": "db-42"}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resend_truncates_before_the_user_message_by_id():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.resendUserMessage(target)", roles, 2, True, db_ids=True)
    result = _run_node(script)
    assert result["truncateBody"] == {"before_msg_id": "db-42"}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_edit_truncates_before_the_user_message_by_id():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.editUserMessage(target)", roles, 2, True, _EDIT_AND_SEND, db_ids=True)
    result = _run_node(script)
    assert result["truncateBody"] == {"before_msg_id": "db-42"}
    assert result["sendClicks"] == ["edited question"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_fork_choice_forks_before_the_user_message_by_id():
    roles = ["user", "ai", "user", "ai", "user", "ai"]
    script = _scenario_script("chat.regenerateFrom(target)", roles, 3, "alternate", db_ids=True)
    result = _run_node(script)
    assert result["truncateCalled"] is False
    assert result["forkBody"]["before_msg_id"] == "db-42"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_fork_conversation_forks_through_the_reply_by_id():
    roles = ["user", "ai", "user", "ai"]
    script = _scenario_script("chat.forkFrom(target)", roles, 1, True, db_ids=True)
    result = _run_node(script)
    assert result["forkBody"]["through_msg_id"] == "db-41"


# A refused cut (the server can't find the message id, e.g. one a previous
# regenerate already deleted) must stop the action. Sending anyway stacked a
# second reply under the same message.

@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regenerate_stops_when_the_server_refuses_the_cut():
    roles = ["user", "ai"]
    script = _scenario_script("chat.regenerateFrom(target)", roles, 1, True, db_ids=True, refuse_truncate=True)
    result = _run_node(script)
    assert result["truncateCalled"] is True
    assert result["sendClicks"] == []


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_edit_stops_when_the_server_refuses_the_cut():
    roles = ["user", "ai"]
    script = _scenario_script("chat.editUserMessage(target)", roles, 0, True, _EDIT_AND_SEND, db_ids=True, refuse_truncate=True)
    result = _run_node(script)
    assert result["truncateCalled"] is True
    assert result["sendClicks"] == []

