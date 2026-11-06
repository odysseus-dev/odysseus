"""The MLX VLM serve command, built by the real _buildServeCmd.

Vision models now route to a `mlx_vlm` backend (see the commit that added
services/hwfit/vision.py). This pins the command that backend produces.

Verified against mlx-vlm 0.7.9 installed in /Users/lp1/AI/odysseus/.venv-mlx-vlm:
`python -m mlx_vlm.server --help` accepts --host (default 0.0.0.0), --port
(default 8080), --model (preload, e.g. mlx-community/Qwen2.5-VL-3B-Instruct-4bit)
and --max-tokens. The upstream README shows both `mlx_vlm.server` and
`python -m mlx_vlm server`; the `-m mlx_vlm.server` form is what this builder
emits, because every other Python backend here is launched through the venv's
python by absolute path, which sidesteps PATH entirely.

The port default is 8090, not mlx-vlm's own 8080: the MLX text backend already
defaults to 8080 in this form, and two MLX servers colliding on one port by
default is a trap.

Driven through `node --input-type=module`. cookbook.js cannot be imported under
node (browser-coupled imports), so the builder and the three small helpers it
needs are sliced out of the real file by brace matching and executed — the
shipped code, not a copy.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_COOKBOOK = _REPO / "static" / "js" / "cookbook.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _slice_function(source, signature):
    """Return `signature` plus its body, ending at the closing `}` in column 0.

    Brace counting is not usable here: _buildServeCmd embeds braces inside
    string and regex literals, so a naive counter never balances. Every
    top-level function in this file closes with an unindented `}`.
    """
    start = source.index(signature)
    lines = source[start:].split("\n")
    for n, line in enumerate(lines):
        if n and line == "}":
            return "\n".join(lines[:n + 1])
    raise AssertionError(f"no column-0 close found after {signature!r}")


def _build(form, model_name, backend, *, windows=False, env_state=None):
    src = _COOKBOOK.read_text(encoding="utf-8")
    pieces = [
        _slice_function(src, "export function _buildServeCmd(f, modelName, backend) {"),
        _slice_function(src, "export function _shellQuote(value) {"),
        _slice_function(src, "function _venvRootFromPath(path) {"),
        _slice_function(src, "function _venvLooksWrongForPlatform(path, platform) {"),
        _slice_function(src, "function _numField(value) {"),
        _slice_function(src, "function _listField(value) {"),
    ]
    body = "\n".join(p.replace("export function", "function", 1) for p in pieces)
    state = env_state if env_state is not None else {"env": "none", "envPath": ""}
    js = (
        f"const _envState = {json.dumps(state)};\n"
        f"function _isWindows() {{ return {json.dumps(windows)}; }}\n"
        f"{body}\n"
        f"console.log(JSON.stringify(_buildServeCmd({json.dumps(form)}, "
        f"{json.dumps(model_name)}, {json.dumps(backend)})));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


_MODEL = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"


def test_mlx_vlm_command_shape():
    cmd = _build({}, _MODEL, "mlx_vlm")
    assert "-m mlx_vlm.server" in cmd
    assert f"--model '{_MODEL}'" in cmd
    assert "--host 127.0.0.1" in cmd
    assert "--port 8090" in cmd


def test_mlx_vlm_command_never_invokes_the_text_only_server():
    # The whole point: this must not be mlx_lm. "mlx_vlm.server" does not
    # contain "mlx_lm.server", so assert on the module token directly.
    cmd = _build({}, _MODEL, "mlx_vlm")
    assert "mlx_lm" not in cmd


def test_dedicated_venv_python_is_used_by_absolute_path():
    # mlx-vlm needs mlx>=0.32.4 while the main venv pins 0.32.2, so it lives in
    # its own venv and the launch must point at that interpreter, not PATH.
    cmd = _build({"venv": "/Users/lp1/AI/odysseus/.venv-mlx-vlm"}, _MODEL, "mlx_vlm")
    assert "/Users/lp1/AI/odysseus/.venv-mlx-vlm/bin/python3 -m mlx_vlm.server" in cmd


def test_venv_given_as_an_activate_or_python_path_is_normalized():
    for given in (
        "/Users/lp1/AI/odysseus/.venv-mlx-vlm/bin/activate",
        "/Users/lp1/AI/odysseus/.venv-mlx-vlm/bin/python3",
    ):
        cmd = _build({"venv": given}, _MODEL, "mlx_vlm")
        assert "/Users/lp1/AI/odysseus/.venv-mlx-vlm/bin/python3 -m mlx_vlm.server" in cmd, given


def test_host_toggle_binds_all_interfaces():
    cmd = _build({"host": True}, _MODEL, "mlx_vlm")
    assert "--host 0.0.0.0" in cmd


def test_explicit_port_is_honored():
    cmd = _build({"port": "8099"}, _MODEL, "mlx_vlm")
    assert "--port 8099" in cmd


def test_context_control_maps_to_max_tokens():
    # Same treatment as the MLX text backend: mlx-vlm exposes no vLLM-style
    # --context-length, and --max-tokens is the closest request budget.
    assert "--max-tokens 4096" in _build({"ctx": "4096"}, _MODEL, "mlx_vlm")
    assert "--max-tokens" not in _build({"ctx": "auto"}, _MODEL, "mlx_vlm")


def test_model_name_is_shell_quoted():
    cmd = _build({}, "weird/model's-name", "mlx_vlm")
    assert "'weird/model'\\''s-name'" in cmd


def test_mlx_text_backend_is_unchanged():
    # Regression guard: the text branch keeps its own module and its own 8080.
    cmd = _build({}, "mlx-community/Qwen3-8B-4bit", "mlx")
    assert "-m mlx_lm.server" in cmd
    assert "--port 8080" in cmd
    assert "mlx_vlm" not in cmd
