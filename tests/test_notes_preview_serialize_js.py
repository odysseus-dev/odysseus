"""Round-trip fidelity for the notes preview editor's DOM→markdown serializer.

The preview window is a contenteditable WYSIWYG editor: every edit re-serializes
the whole body via `_serializePreviewBody` and PATCHes the result, so anything
the serializer can't represent is silently lost on the next keystroke. These
tests pin the constructs that were previously dropped — fenced-code language,
tables, and nested-list indentation — by running the REAL serializer functions
(extracted from notes.js) against a minimal fake DOM.

notes.js can't be node-imported in isolation (heavy browser import chain), so we
slice out just the pure serializer helpers and exec them with a tiny hand-built
DOM. A separate source-assertion test covers the event-bound fixes (paste,
keyboard a11y) that can't be exercised without a real browser.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = (_REPO / "static" / "js" / "notes.js").read_text(encoding="utf-8")
_HAS_NODE = shutil.which("node") is not None

_FN_RE = re.compile(r"^(?:async )?function (\w+)\(", re.M)


def _extract_fn(name: str) -> str:
    starts = [(m.start(), m.group(1)) for m in _FN_RE.finditer(_SRC)]
    for idx, (pos, nm) in enumerate(starts):
        if nm == name:
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(_SRC)
            return _SRC[pos:end]
    raise KeyError(name)


_FAKE_DOM = r"""
function _textContent(node){
  if(!node) return '';
  if(node.nodeType===3) return node.nodeValue || '';
  return (node.childNodes||[]).map(_textContent).join('');
}
function _matches(node, token){
  token=token.trim();
  if(token.startsWith('.')) return node.classList && node.classList.contains(token.slice(1));
  return node.tagName === token.toUpperCase();
}
function _walk(node, fn){
  (node.childNodes||[]).forEach(c=>{ if(c && c.nodeType===1){ fn(c); _walk(c,fn);} });
}
function _qs(root, sel, all){
  const tokens = sel.split(',').map(s=>s.trim());
  const res=[];
  _walk(root, (n)=>{ if(tokens.some(t=>_matches(n,t))) res.push(n); });
  return all ? res : (res[0]||null);
}
function txt(s){ return { nodeType:3, nodeValue:s, parentElement:null }; }
function el(tag, opts){
  opts = opts || {};
  const cls = opts.cls || [];
  const attrs = opts.attrs || {};
  const style = opts.style || {};
  let children = opts.children || [];
  if (opts.text !== undefined) children = [txt(opts.text)];
  const node = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    classList: { contains: (c) => cls.indexOf(c) !== -1 },
    style: style,
    getAttribute: (n) => (Object.prototype.hasOwnProperty.call(attrs,n) ? attrs[n] : null),
    childNodes: children,
    parentElement: null,
    get children(){ return this.childNodes.filter(n => n.nodeType === 1); },
    get textContent(){ return _textContent(this); },
    querySelector(sel){ return _qs(this, sel, false); },
    querySelectorAll(sel){ return _qs(this, sel, true); },
  };
  children.forEach(c => { if (c) c.parentElement = node; });
  return node;
}
"""


def _run(cases_js: str):
    fns = "\n".join(_extract_fn(n) for n in
                     ("_imgWidthPct", "_imgToMd", "_previewInlineMd", "_serializePreviewBody"))
    script = _FAKE_DOM + "\n" + fns + "\n" + cases_js
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO, capture_output=True, timeout=15, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def test_code_fence_language_preserved(node_available):
    out = _run(r"""
      const code = el('code', { attrs: { 'data-lang': 'python' }, text: 'print(1)\n' });
      const pre = el('pre', { children: [ code, el('button', { cls:['copy-code'], text:'' }) ] });
      const body = el('div', { children: [pre] });
      console.log(JSON.stringify({ md: _serializePreviewBody(body) }));
    """)
    assert out["md"] == "```python\nprint(1)\n```"


def test_table_round_trips_to_pipe_table(node_available):
    out = _run(r"""
      const head = el('tr', { children: [ el('th',{text:'A'}), el('th',{text:'B'}) ] });
      const row  = el('tr', { children: [ el('td',{text:'1'}), el('td',{text:'2'}) ] });
      const table = el('table', { children: [head, row] });
      const body = el('div', { children: [table] });
      console.log(JSON.stringify({ md: _serializePreviewBody(body) }));
    """)
    assert out["md"] == "| A | B |\n| --- | --- |\n| 1 | 2 |"


def test_nested_list_indentation_preserved(node_available):
    out = _run(r"""
      const inner = el('ul', { children: [ el('li', { children:[txt('Child')] }) ] });
      const parentLi = el('li', { children: [ txt('Parent'), inner ] });
      const ul = el('ul', { children: [parentLi] });
      const body = el('div', { children: [ul] });
      console.log(JSON.stringify({ md: _serializePreviewBody(body) }));
    """)
    # Parent on its own line; the nested item indented two spaces, not flattened.
    assert out["md"] == "- Parent\n  - Child"


def test_task_lines_and_inline_still_serialize(node_available):
    out = _run(r"""
      const li = el('li', { cls:['md-task'], attrs:{'data-done':'1'}, children:[
        el('span', { cls:['md-task-box'], attrs:{'aria-checked':'true'} }),
        el('span', { cls:['md-task-text'], children:[txt('Done item')] }),
      ]});
      const ul = el('ul', { cls:['md-tasklist'], children:[li] });
      const p = el('p', { children: [
        el('strong', { children:[txt('Hi')] }),
        txt(' '),
        el('a', { attrs:{ href:'https://x' }, children:[txt('link')] }),
      ]});
      const body = el('div', { children: [ul, p] });
      console.log(JSON.stringify({ md: _serializePreviewBody(body) }));
    """)
    assert "- [x] Done item" in out["md"]
    assert "**Hi** [link](https://x)" in out["md"]


def test_preview_editor_event_fixes_present():
    """Source-assertions for the event-bound fixes (not runnable without a DOM)."""
    # Paste-as-plaintext: pasted HTML must never land live in the editor.
    assert "addEventListener('paste'" in _SRC
    assert "getData('text/plain')" in _SRC
    # Keyboard a11y: checkbox boxes are focusable and toggle on Enter/Space.
    assert "_togglePreviewTaskBox" in _SRC
    assert "tabindex" in _SRC
    # The premature shared-note live-sync poll is gone (deferred to slice 4).
    assert "_startNotesLiveSync" not in _SRC
    assert "setInterval(_liveSyncNotes" not in _SRC
