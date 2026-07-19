"""Behavioral test for the Added Models "Tools: Auto/On/Off" toggle (#5206).

admin.js can't be imported standalone (browser-only deps), so, same approach as
the other tests/test_*_js.py, we extract the click-handler body from source and
run it under node with a mocked btn / fetch / loadEndpoints. We assert:

  * the PATCH body walks null -> true -> false -> null across three clicks
    (Auto -> On -> Off -> Auto), reading the state the server re-rendered each
    time, and
  * a failed fetch still calls loadEndpoints() so the label re-syncs from the
    server instead of being left stale.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_ADMIN_JS = _REPO / "static" / "js" / "admin.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _extract_click_handler_body(src: str, anchor: str, event: str = "click") -> str:
    """Body (without outer braces) of the `async (e) =>` handler registered for
    `event` in the block that follows `anchor`, via a quote-aware brace match."""
    start = src.index(anchor)
    marker = f"addEventListener('{event}', async (e) =>"
    brace = src.index("{", src.index(marker, start))
    i, depth, quote, escaped = brace + 1, 1, None, False
    while i < len(src):
        c = src[i]
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace + 1:i]
        i += 1
    raise AssertionError("unbalanced braces in handler body")


def _handler_body() -> str:
    return _extract_click_handler_body(_ADMIN_JS.read_text(), "data-adm-tools-ep]').forEach")


def _node_eval(source: str):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=_REPO, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_cycle_walks_null_true_false_null():
    body = _handler_body()
    source = f"""
    const HANDLER = async (e, btn, fetch, loadEndpoints) => {{ {body} }};
    const sent = [];
    let serverState = null;               // supports_tools stored server-side
    const label = (s) => s === true ? 'on' : s === false ? 'off' : 'auto';
    const btn = {{ dataset: {{ admToolsEp: 'ep1', get admToolsState() {{ return label(serverState); }} }} }};
    const e = {{ stopPropagation() {{}} }};
    async function fetchMock(url, opts) {{
      const parsed = JSON.parse(opts.body);
      sent.push(parsed.supports_tools);
      serverState = parsed.supports_tools;   // server accepts and stores it
      return {{ ok: true }};
    }}
    async function loadEndpoints() {{ /* re-render re-derives the button state */ }}
    await HANDLER(e, btn, fetchMock, loadEndpoints);
    await HANDLER(e, btn, fetchMock, loadEndpoints);
    await HANDLER(e, btn, fetchMock, loadEndpoints);
    console.log(JSON.stringify(sent));
    """
    assert _node_eval(source) == [True, False, None]


def test_patch_targets_the_endpoint_id_and_method():
    body = _handler_body()
    source = f"""
    const HANDLER = async (e, btn, fetch, loadEndpoints) => {{ {body} }};
    let call = null;
    const btn = {{ dataset: {{ admToolsEp: 'abc123', admToolsState: 'auto' }} }};
    async function fetchMock(url, opts) {{ call = {{ url, method: opts.method }}; return {{ ok: true }}; }}
    await HANDLER({{ stopPropagation() {{}} }}, btn, fetchMock, async () => {{}});
    console.log(JSON.stringify(call));
    """
    call = _node_eval(source)
    assert call["url"] == "/api/model-endpoints/abc123"
    assert call["method"] == "PATCH"


def test_failed_fetch_still_resyncs():
    body = _handler_body()
    source = f"""
    const HANDLER = async (e, btn, fetch, loadEndpoints) => {{ {body} }};
    let loaded = 0;
    const btn = {{ dataset: {{ admToolsEp: 'ep1', admToolsState: 'auto' }} }};
    async function fetchThrows() {{ throw new Error('network down'); }}
    async function loadEndpoints() {{ loaded += 1; }}
    await HANDLER({{ stopPropagation() {{}} }}, btn, fetchThrows, loadEndpoints);
    console.log(JSON.stringify({{ loaded }}));
    """
    # The handler must not let a network error skip the re-sync.
    assert _node_eval(source) == {"loaded": 1}
