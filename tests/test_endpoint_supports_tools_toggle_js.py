"""Behavioral test for issue #5048 — a Settings switch that lets local model
endpoints use tools.

Manually-added local endpoints store supports_tools=NULL, which the agent-loop
heuristic treats as "no native tool schemas" for Ollama URLs — so Agent mode
describes tool calls in prose instead of running them. The fix adds a
per-local-endpoint switch in admin.js that PATCHes supports_tools; the backend
route, DB column, and heuristic already honor the value (the True override is
covered by tests/test_tool_support_heuristic.py).

admin.js can't be imported standalone (browser-only deps), so — same approach as
tests/test_local_endpoint_api_key_js.py — we extract the handler body from source
and run it under node with a mocked fetch, asserting the outgoing request.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "static" / "js" / "admin.js"
_HAS_NODE = shutil.which("node") is not None

_MARKER = "async function _setEndpointSupportsTools(epId, on)"


def _extract_handler_body(src: str, marker: str) -> str:
    """Return the body (without the outer braces) of the function that
    immediately follows `marker` in `src`, using a quote-aware brace matcher."""
    start = src.index(marker) + len(marker)
    brace = src.index("{", start)
    i = brace + 1
    depth = 1
    quote = None
    escaped = False
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
    raise AssertionError(f"unbalanced braces after marker: {marker!r}")


# On resolve the harness prints "OK:<calls-json>"; an unhandled rejection prints
# "UNHANDLED:<msg>" and exits non-zero — which is exactly what the swallow guards
# against, so the failure test asserts we never hit that path.
_HARNESS = """
let calls = [];
async function fetch(url, opts) {{ {fetch_body} }}
const epId = "ep-42";
const on = {on};
async function run() {{ {body} }}
run().then(() => console.log("OK:" + JSON.stringify(calls)))
     .catch((e) => {{ console.log("UNHANDLED:" + e.message); process.exit(3); }});
"""

_FETCH_OK = (
    "calls.push({ url, method: opts.method, body: JSON.parse(opts.body) });"
    " return { ok: true, async json() { return {}; } };"
)
_FETCH_THROWS = "throw new Error('network down');"


def _run(on_literal: str, fetch_body: str) -> subprocess.CompletedProcess:
    handler = _extract_handler_body(_ADMIN_JS.read_text(encoding="utf-8"), _MARKER)
    js = _HARNESS.format(on=on_literal, body=handler, fetch_body=fetch_body)
    return subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )


def _calls(on_literal: str) -> list:
    proc = _run(on_literal, _FETCH_OK)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    out = proc.stdout.strip()
    assert out.startswith("OK:"), f"unexpected node output: {out}"
    return json.loads(out[len("OK:"):])


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_switch_on_patches_supports_tools_true():
    calls = _calls("true")
    assert len(calls) == 1
    assert calls[0]["method"] == "PATCH"
    assert calls[0]["url"] == "/api/model-endpoints/ep-42"
    assert calls[0]["body"] == {"supports_tools": True}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_switch_off_patches_null_not_false():
    # Off sends null (auto-detect), never false — so this local-only switch can
    # never disable a cloud model the heuristic already treats as tool-capable.
    calls = _calls("false")
    assert calls[0]["body"] == {"supports_tools": None}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_switch_patch_failure_is_swallowed():
    # A failed PATCH must not surface as an unhandled rejection: the handler
    # resolves so the caller's loadEndpoints() can re-sync the switch from the
    # server (reverting the checkbox) instead of leaving a stale visual state.
    proc = _run("true", _FETCH_THROWS)
    assert proc.returncode == 0, f"unhandled rejection: {proc.stdout} {proc.stderr}"
    assert proc.stdout.strip().startswith("OK:")


def test_switch_rendered_and_gated_to_local_endpoints():
    """Source-text check (the narrow TESTING_STANDARD.md exception): the row
    template nests template literals and depends on many DOM/helper globals, so
    it can't be practically rendered standalone. We assert the switch is wired to
    the endpoint id, reflects persisted state, and sits inside the local guard."""
    src = _ADMIN_JS.read_text(encoding="utf-8")
    assert 'data-adm-tools-ep="${ep.id}"' in src
    assert "ep.supports_tools === true ? 'checked' : ''" in src
    tools_pos = src.index("data-adm-tools-ep")
    guard = src[src.rindex("${", 0, tools_pos):tools_pos]
    assert guard.startswith("${category === 'local' ?"), (
        f"tool switch must be gated to local endpoints, got: {guard[:60]!r}"
    )
