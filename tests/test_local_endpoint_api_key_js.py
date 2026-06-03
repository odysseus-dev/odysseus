"""Behavioral test for issue #353 — Local LLM endpoints behind an API key.

The admin "Local" add/test form previously sent only `base_url` (+ model_type),
so a self-hosted endpoint protected by an API key could never be added — it just
errored out. The backend `POST /api/model-endpoints` and `/model-endpoints/test`
already accept an `api_key` form field; the fix wires the new `adm-epLocalApiKey`
input into the local Test and Add handlers.

admin.js can't be imported standalone (browser-only deps), so — same approach as
tests/test_local_endpoint_js.py — we extract the two click-handler bodies from
source and run them under node with mocked DOM/FormData/fetch, asserting the
outgoing form data contains `api_key` exactly when the key field is filled.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "static" / "js" / "admin.js"
_INDEX_HTML = _REPO / "static" / "index.html"
_HAS_NODE = shutil.which("node") is not None


def _extract_handler_body(src: str, marker: str) -> str:
    """Return the body (without the outer braces) of the arrow function that
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


_HARNESS = """
let appended = [];
class FormData {{ append(k, v) {{ appended.push([k, String(v)]); }} }}
const FIELDS = {fields};
function el(id) {{
  if (!(id in FIELDS)) return null;
  return {{
    get value() {{ return FIELDS[id]; }},
    set value(x) {{ FIELDS[id] = x; }},
    disabled: false, textContent: '',
    classList: {{ add() {{}}, remove() {{}} }},
  }};
}}
function _endpointMsg() {{ return {{ textContent: '', className: '' }}; }}
function _normalizeBaseUrl(u) {{ return u; }}
function _renderEndpointTestResult() {{}}
async function loadEndpoints() {{}}
async function _selectAddedModelInChat() {{}}
let _recentlyAddedEpId = null;
const localTestBtn = {{ disabled: false, textContent: '' }};
const localAddBtn = {{ disabled: false, textContent: '' }};
async function fetch() {{
  return {{ ok: true, async json() {{ return {{ id: 'x', models: [], online: true, status: 'ok' }}; }} }};
}}
async function run() {{ {body} }}
run().then(() => console.log(JSON.stringify(appended)))
     .catch((e) => {{ console.error(e); process.exit(2); }});
"""


def _run_handler(body: str, fields: dict) -> list:
    js = _HARNESS.format(fields=json.dumps(fields), body=body)
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}\n---\n{js}"
    return json.loads(proc.stdout.strip())


def _handler(marker: str) -> str:
    return _extract_handler_body(_ADMIN_JS.read_text(encoding="utf-8"), marker)


_TEST_MARKER = "localTestBtn.addEventListener('click', async () => "
_ADD_MARKER = "localAddBtn.addEventListener('click', async () => "


def test_local_form_has_api_key_input():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    pos = html.find('id="adm-epLocalApiKey"')
    assert pos != -1, "adm-epLocalApiKey input missing from index.html"
    # Isolate the enclosing <input ...> tag and require it to be a masked field,
    # like the cloud form's API-key input.
    tag = html[html.rfind("<input", 0, pos):html.index(">", pos) + 1]
    assert 'type="password"' in tag, f"local API key must be a password input: {tag}"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("marker", [_TEST_MARKER, _ADD_MARKER])
def test_api_key_sent_when_filled(marker):
    fields = {"adm-epLocalUrl": "http://localhost:8002/v1",
              "adm-epLocalApiKey": "sk-secret", "adm-epLocalType": "llm"}
    appended = dict(_run_handler(_handler(marker), fields))
    assert appended.get("base_url") == "http://localhost:8002/v1"
    assert appended.get("api_key") == "sk-secret", f"api_key not sent: {appended}"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("marker", [_TEST_MARKER, _ADD_MARKER])
def test_api_key_omitted_when_blank(marker):
    fields = {"adm-epLocalUrl": "http://localhost:8002/v1",
              "adm-epLocalApiKey": "", "adm-epLocalType": "llm"}
    keys = [k for k, _ in _run_handler(_handler(marker), fields)]
    assert "base_url" in keys
    assert "api_key" not in keys, "blank key must not be appended (avoids empty Bearer)"
