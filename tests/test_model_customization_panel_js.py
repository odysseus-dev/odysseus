"""Seam (g): the Per-Model Settings panel helpers in
static/js/settings/modelCustomization.js.

Spec: openspec/changes/add-model-customization/specs/model-customization/spec.md
(requirements "Per-model settings box in Added Models" and "Panel save
feedback"). The DOM wiring (`initModelCustomization`) is verified manually; the
four pure helpers it relies on are driven here through
`node --input-type=module`, the same idiom as test_app_config_shared_fetch_js.py.
The module has no imports, so inlining its source is exact. Plain objects
stand in for the form controls.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "settings" / "modelCustomization.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _MODULE.read_text(encoding="utf-8") if _MODULE.exists() else ""

_CONTROLS = r"""
const names = ['temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty',
  'repeat_penalty', 'num_ctx', 'thinking'];
const controls = Object.fromEntries(names.map((n) => [n, { value: '', disabled: false }]));
const snapshot = () => Object.fromEntries(
  names.map((n) => [n, { value: controls[n].value, disabled: controls[n].disabled }]));
"""


def _run(body: str):
    js = _SRC + "\n" + _CONTROLS + "\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


needs_node = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


@needs_node
def test_save_payload_drops_blanks_and_parses_numbers():
    body = """
    controls.temperature.value = '0.6';
    controls.top_k.value = '40';
    controls.num_ctx.value = ' 16384 ';
    controls.thinking.value = 'on';
    controls.top_p.value = '   ';
    console.log(JSON.stringify(buildSettingsPayload({
      endpoint_id: 'lab-ollama', model: 'qwen3:8b', controls })));
    """
    assert _run(body) == {
        "endpoint_id": "lab-ollama",
        "model": "qwen3:8b",
        "settings": {"temperature": 0.6, "top_k": 40, "num_ctx": 16384, "thinking": "on"},
    }


@needs_node
def test_save_payload_skips_disabled_controls():
    body = """
    controls.temperature.value = '0.6';
    controls.top_k.value = '40';
    controls.top_k.disabled = true;
    console.log(JSON.stringify(buildSettingsPayload({
      endpoint_id: 'groq', model: 'llama', controls }).settings));
    """
    assert _run(body) == {"temperature": 0.6}


@needs_node
def test_save_payload_with_every_control_blank_clears_the_entry():
    body = """
    console.log(JSON.stringify(buildSettingsPayload({
      endpoint_id: 'groq', model: 'llama', controls }).settings));
    """
    assert _run(body) == {}


@needs_node
def test_unsupported_controls_are_disabled_and_blank():
    body = """
    controls.top_k.value = '40';
    controls.temperature.disabled = true;
    applySupportedFields(controls, ['temperature', 'top_p', 'presence_penalty', 'thinking']);
    console.log(JSON.stringify(snapshot()));
    """
    state = _run(body)
    for name in ("temperature", "top_p", "presence_penalty", "thinking"):
        assert state[name]["disabled"] is False, name
    for name in ("top_k", "min_p", "repeat_penalty", "num_ctx"):
        assert state[name] == {"value": "", "disabled": True}, name


@needs_node
def test_stored_values_are_shown_and_blanks_stay_blank():
    body = """
    controls.min_p.value = '0.2';
    fillControls(controls, { temperature: 0.6, top_k: 40, thinking: 'off' });
    console.log(JSON.stringify(snapshot()));
    """
    state = _run(body)
    assert state["temperature"]["value"] == "0.6"
    assert state["top_k"]["value"] == "40"
    assert state["thinking"]["value"] == "off"
    for name in ("top_p", "min_p", "presence_penalty", "repeat_penalty", "num_ctx"):
        assert state[name]["value"] == "", name


@needs_node
def test_rejected_save_shows_the_server_reason():
    body = """
    console.log(JSON.stringify([
      saveErrorMessage({ detail: 'temperature must be between 0 and 2' }),
      saveErrorMessage({ detail: [{ loc: ['body', 'model'], msg: 'Field required' }] }),
      saveErrorMessage(null),
    ]));
    """
    assert _run(body) == [
        "temperature must be between 0 and 2",
        "Field required",
        "Could not save the model settings",
    ]
