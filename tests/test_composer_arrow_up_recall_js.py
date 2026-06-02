"""Pin ArrowUp recall on the chat composer (static/js/composerArrowUpRecall.js).

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_reply_recipients_js.py). Skips
when `node` is not installed rather than failing.

Locks in: empty composer recalls last user message; non-empty composer is
untouched; multiline caret navigation is not hijacked; Shift/Alt/Ctrl/Meta+ArrowUp
are ignored; IME composition does not trigger recall; last message is read from
#chat-history (dataset.raw), not session sidebar metadata.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "composerArrowUpRecall.js"
_HELPER_URL = _HELPER.as_uri()
_HAS_NODE = shutil.which("node") is not None

_HARNESS = r"""
import { wireArrowUpRecall } from 'HELPER_PATH';

function makeComposer(initial = '') {
  const listeners = [];
  const composer = {
    value: initial,
    selectionStart: initial.length,
    selectionEnd: initial.length,
    _arrowUpRecallWired: false,
    addEventListener(type, fn) {
      if (type === 'keydown') listeners.push(fn);
    },
    dispatchKey(opts = {}) {
      let prevented = false;
      const e = {
        key: opts.key ?? 'ArrowUp',
        shiftKey: !!opts.shiftKey,
        altKey: !!opts.altKey,
        ctrlKey: !!opts.ctrlKey,
        metaKey: !!opts.metaKey,
        isComposing: !!opts.isComposing,
        preventDefault() { prevented = true; },
      };
      for (const fn of listeners) fn(e);
      return prevented;
    },
  };
  return composer;
}

function runCase(body) {
  const composer = makeComposer(body.initial ?? '');
  if (body.caret != null) {
    composer.selectionStart = body.caret;
    composer.selectionEnd = body.caretEnd ?? body.caret;
  }
  const last = body.last ?? 'previous message';
  let resized = false;
  wireArrowUpRecall(composer, () => last, {
    autoResize: () => { resized = true; },
  });
  const prevented = composer.dispatchKey(body.event ?? {});
  return {
    value: composer.value,
    selectionStart: composer.selectionStart,
    selectionEnd: composer.selectionEnd,
    prevented,
    resized,
  };
}

const cases = CASES_JSON;
const results = cases.map(runCase);
console.log(JSON.stringify(results));
""".replace("HELPER_PATH", _HELPER_URL)


def _run(cases: list) -> list:
    js = _HARNESS.replace("CASES_JSON", json.dumps(cases))
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_empty_composer_recalls_last_user_message():
    out = _run([{"initial": "", "last": "hello again"}])[0]
    assert out["value"] == "hello again"
    assert out["selectionStart"] == len("hello again")
    assert out["selectionEnd"] == len("hello again")
    assert out["prevented"] is True
    assert out["resized"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_non_empty_composer_does_not_recall():
    out = _run([{"initial": "draft in progress", "last": "ignored"}])[0]
    assert out["value"] == "draft in progress"
    assert out["prevented"] is False
    assert out["resized"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_whitespace_only_composer_is_not_empty():
    out = _run([{"initial": "   ", "last": "ignored"}])[0]
    assert out["value"] == "   "
    assert out["prevented"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_multiline_caret_navigation_preserved():
    # Caret on line 2 — ArrowUp must not recall or preventDefault.
    text = "line one\nline two"
    out = _run([{"initial": text, "caret": len(text), "last": "ignored"}])[0]
    assert out["value"] == text
    assert out["selectionStart"] == len(text)
    assert out["prevented"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_modified_arrow_up_ignored():
    cases = [
        {"initial": "", "event": {"shiftKey": True}},
        {"initial": "", "event": {"altKey": True}},
        {"initial": "", "event": {"ctrlKey": True}},
        {"initial": "", "event": {"metaKey": True}},
    ]
    for out in _run(cases):
        assert out["value"] == ""
        assert out["prevented"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_ime_composition_does_not_trigger_recall():
    out = _run([{"initial": "", "event": {"isComposing": True}, "last": "ignored"}])[0]
    assert out["value"] == ""
    assert out["prevented"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_no_recall_when_last_message_missing():
    out = _run([{"initial": "", "last": ""}])[0]
    assert out["value"] == ""
    assert out["prevented"] is False
    assert out["resized"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_wire_is_idempotent():
    js = f"""
    import {{ wireArrowUpRecall }} from '{_HELPER_URL}';
    const composer = {{ _arrowUpRecallWired: false, addEventListener() {{}} }};
    const ok1 = wireArrowUpRecall(composer, () => 'x');
    const ok2 = wireArrowUpRecall(composer, () => 'y');
    console.log(JSON.stringify({{ ok1, ok2, wired: composer._arrowUpRecallWired }}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"ok1": True, "ok2": True, "wired": True}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_get_last_user_message_from_chat_history():
    js = f"""
    import {{ getLastUserMessageFromChatHistory }} from '{_HELPER_URL}';

    const chatBox = {{
      id: 'chat-history',
      querySelectorAll(sel) {{
        if (sel !== '.msg-user') return [];
        return [
          {{ dataset: {{ raw: 'first' }}, querySelector: () => null }},
          {{ dataset: {{ raw: 'last raw' }}, querySelector: () => null }},
        ];
      }},
    }};

    const doc = {{
      getElementById(id) {{ return id === 'chat-history' ? chatBox : null; }},
    }};

    console.log(JSON.stringify({{
      fromChat: getLastUserMessageFromChatHistory(doc),
      fromBox: getLastUserMessageFromChatHistory(chatBox),
      empty: getLastUserMessageFromChatHistory({{ getElementById: () => null }}),
      noUsers: getLastUserMessageFromChatHistory({{
        getElementById: () => ({{ querySelectorAll: () => [] }}),
      }}),
    }}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {
        "fromChat": "last raw",
        "fromBox": "last raw",
        "empty": "",
        "noUsers": "",
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_integration_recalls_from_chat_history_dom():
    js = f"""
    import {{
      wireArrowUpRecall,
      getLastUserMessageFromChatHistory,
    }} from '{_HELPER_URL}';

    const chatBox = {{
      id: 'chat-history',
      querySelectorAll(sel) {{
        if (sel !== '.msg-user') return [];
        return [{{ dataset: {{ raw: 'stored prompt' }}, querySelector: () => null }}];
      }},
    }};
    const doc = {{ getElementById: (id) => (id === 'chat-history' ? chatBox : null) }};

    const listeners = [];
    const composer = {{
      value: '',
      selectionStart: 0,
      selectionEnd: 0,
      _arrowUpRecallWired: false,
      addEventListener(type, fn) {{ if (type === 'keydown') listeners.push(fn); }},
    }};
    wireArrowUpRecall(composer, () => getLastUserMessageFromChatHistory(doc));
    let prevented = false;
    listeners[0]({{
      key: 'ArrowUp',
      shiftKey: false,
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      isComposing: false,
      preventDefault() {{ prevented = true; }},
    }});
    console.log(JSON.stringify({{ value: composer.value, prevented }}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"value": "stored prompt", "prevented": True}
