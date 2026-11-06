"""The mlx-vlm serve preflight, executed as the shell it actually generates.

mlx-vlm requires mlx>=0.32.4, newer than the main venv pins, so it normally
lives in a venv of its own and the serve command points at that interpreter by
absolute path. The preflight has to resolve that interpreter out of the command
and check the import *there* — checking the ambient python3 would pass or fail
for the wrong reason.

These tests run the generated script with `sh` and assert on its real exit
status and output, rather than matching substrings in the generated text. The
"module present" case needs an interpreter that has mlx_vlm, which is a local
install, so it skips when there isn't one.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from routes.cookbook_helpers import _append_mlx_vlm_preflight_lines

_MLX_VLM_VENV_PY = Path("/Users/lp1/AI/odysseus/.venv-mlx-vlm/bin/python3")


def _run_preflight(cmd, tmp_path):
    """Generate the preflight, run it, return (exit_code, output)."""
    lines = ["ODYSSEUS_PREFLIGHT_EXIT="]
    _append_mlx_vlm_preflight_lines(lines, cmd)
    # Mirror what the runner does with the variable: a non-empty value is the
    # launch's exit status, and the serve never starts.
    lines.append('if [ -n "$ODYSSEUS_PREFLIGHT_EXIT" ]; then exit "$ODYSSEUS_PREFLIGHT_EXIT"; fi')
    lines.append('echo "[odysseus] preflight passed"')
    script = tmp_path / "preflight.sh"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_missing_module_refuses_the_launch_with_a_named_interpreter(tmp_path):
    # The interpreter running these tests is the main venv, which deliberately
    # does NOT have mlx_vlm — installing it there would upgrade mlx under the
    # working mlx_lm text models.
    cmd = f"{sys.executable} -m mlx_vlm.server --model mlx-community/Qwen2.5-VL-3B-Instruct-4bit --host 127.0.0.1 --port 8090"
    code, out = _run_preflight(cmd, tmp_path)

    assert code == 127, out
    assert "ERROR: mlx-vlm is not installed in the launch Python" in out
    assert sys.executable in out            # says which interpreter, by path
    assert "ModuleNotFoundError" in out     # and passes the real import error through
    assert "preflight passed" not in out


def test_the_refusal_never_suggests_or_falls_back_to_mlx_lm(tmp_path):
    # A fallback to mlx_lm would start a text-only server for a model whose
    # whole point is taking an image: healthy-looking and silently useless.
    cmd = f"{sys.executable} -m mlx_vlm.server --model some/vision-model --host 127.0.0.1 --port 8090"
    _, out = _run_preflight(cmd, tmp_path)

    assert "mlx_lm" not in out


def test_it_checks_the_interpreter_from_the_command_not_the_ambient_one(tmp_path):
    # A bare `python3` in the command must be reported as such, rather than the
    # preflight silently probing whichever python3 happens to be on PATH.
    code, out = _run_preflight("python3 -m mlx_vlm.server --model m --port 8090", tmp_path)
    assert "[odysseus] mlx-vlm launch Python: python3" in out
    # Whatever the ambient python3 is, the verdict must be about *it*.
    probe = subprocess.run(
        ["python3", "-c", "import mlx_vlm"], capture_output=True, text=True, timeout=60,
    )
    assert (code == 0) == (probe.returncode == 0), out


@pytest.mark.skipif(not _MLX_VLM_VENV_PY.exists(),
                    reason="no local venv with mlx-vlm installed")
def test_present_module_lets_the_launch_through(tmp_path):
    cmd = f"{_MLX_VLM_VENV_PY} -m mlx_vlm.server --model mlx-community/Qwen2.5-VL-3B-Instruct-4bit --host 127.0.0.1 --port 8090"
    code, out = _run_preflight(cmd, tmp_path)

    assert code == 0, out
    assert "preflight passed" in out
    assert str(_MLX_VLM_VENV_PY) in out
    assert "ERROR" not in out


def test_a_quoted_model_name_does_not_break_the_interpreter_extraction(tmp_path):
    # _buildServeCmd shell-quotes the model name; the extractor splits with
    # shlex, so a quoted argument must not be mistaken for the interpreter.
    cmd = f"{sys.executable} -m mlx_vlm.server --model 'weird/model name' --host 127.0.0.1 --port 8090"
    _, out = _run_preflight(cmd, tmp_path)
    assert f"launch Python: {sys.executable}" in out
