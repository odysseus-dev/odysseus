import json
import subprocess
import sys

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _cached_model_scan_script,
    _append_serve_exit_code_lines,
    _append_serve_preflight_exit_lines,
    _local_tooling_path_export,
    _safe_env_prefix,
    _validate_gpus,
    _validate_repo_id,
    _validate_serve_model_id,
    _validate_ssh_port,
)


def test_safe_env_prefix_accepts_quoted_venv_path():
    assert (
        _safe_env_prefix("source '~/vllm-env/bin/activate'")
        == '[ -f "$HOME/vllm-env/bin/activate" ] && source "$HOME/vllm-env/bin/activate" || true'
    )


def test_safe_env_prefix_leaves_compound_conda_prefix_unchanged():
    prefix = 'eval "$(conda shell.bash hook)" && conda activate qwen35'
    assert _safe_env_prefix(prefix) == prefix


def test_safe_env_prefix_rejects_freeform_shell():
    with pytest.raises(HTTPException):
        _safe_env_prefix("echo ok; curl https://example.invalid")


def test_safe_env_prefix_accepts_powershell_activation_path():
    assert (
        _safe_env_prefix("& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'")
        == "& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'"
    )


def test_validate_ssh_port_rejects_shell_payload():
    with pytest.raises(HTTPException):
        _validate_ssh_port("22; touch /tmp/pwned")
    assert _validate_ssh_port("2222") == "2222"


def test_validate_gpus_accepts_indexes_only():
    assert _validate_gpus("0,1,2") == "0,1,2"
    with pytest.raises(HTTPException):
        _validate_gpus("0; rm -rf /")


def test_validate_repo_id_stays_strict_for_hf_downloads():
    assert _validate_repo_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    with pytest.raises(HTTPException):
        _validate_repo_id("DeepSeek-R1-UD-IQ4_XS")


def test_validate_serve_model_id_accepts_cached_local_model_names():
    assert _validate_serve_model_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert _validate_serve_model_id("DeepSeek-R1-UD-IQ4_XS") == "DeepSeek-R1-UD-IQ4_XS"
    with pytest.raises(HTTPException):
        _validate_serve_model_id("../escape")


def test_local_tooling_path_export_prepends_interpreter_bin():
    """The cookbook runners must see the venv's bin (where `hf`/`python` live)
    so tmux shells can find them without an activated venv."""
    assert (
        _local_tooling_path_export("/opt/venv/bin/python")
        == 'export PATH="/opt/venv/bin:$PATH"'
    )


def test_local_tooling_path_export_preserves_spaces_and_expands_path():
    line = _local_tooling_path_export("/Users/John Smith/.venv/bin/python3")
    assert line == 'export PATH="/Users/John Smith/.venv/bin:$PATH"'
    assert line.endswith(':$PATH"')  # $PATH stays expandable in double quotes


def test_serve_preflight_failure_keeps_tmux_pane_visible():
    """Dependency preflight failures should remain visible in tmux output.

    A bare `exit 127` kills the tmux pane before the browser/status poller can
    capture the helpful error, leaving users with a blank "crashed" card.
    """
    runner_lines = [
        'ODYSSEUS_PREFLIGHT_EXIT=""',
        'echo "ERROR: vLLM is not installed. Open Cookbook -> Dependencies and install vllm on this server, then launch again."',
        'ODYSSEUS_PREFLIGHT_EXIT=127',
    ]
    _append_serve_preflight_exit_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ERROR: vLLM is not installed" in script
    assert 'ODYSSEUS_PREFLIGHT_EXIT=127' in script
    assert 'echo "=== Process exited with code $ODYSSEUS_PREFLIGHT_EXIT ==="' in script
    assert 'exec "${SHELL:-/bin/bash}"' in script
    assert "exit 127" not in script


def test_serve_runner_preserves_command_exit_code():
    """The serve wrapper must capture `$?` before any echo resets it."""
    runner_lines = ["vllm serve Qwen/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000"]
    _append_serve_exit_code_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ODYSSEUS_CMD_EXIT=$?" in script
    assert 'echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="' in script
    assert 'echo "=== Process exited with code $? ==="' not in script


def test_cached_model_scan_reports_plain_dir_gguf(tmp_path):
    """Custom download dirs may sit inside the HF hub cache and contain plain
    per-model folders. They must show up in Serve and keep the GGUF signal."""
    plain = tmp_path / "Qwen3.6-27B"
    plain.mkdir()
    (plain / "Qwen3.6-27B-Q4_K_M.gguf").write_bytes(b"gguf")

    hf_internal = tmp_path / "models--Qwen--Qwen3.6-27B"
    (hf_internal / "snapshots" / "abc").mkdir(parents=True)
    (hf_internal / "snapshots" / "abc" / "model.safetensors").write_bytes(b"safe")

    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(_cached_model_scan_script([str(tmp_path)]), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
    )

    by_repo = {m["repo_id"]: m for m in json.loads(proc.stdout)}
    assert "models--Qwen--Qwen3.6-27B" not in by_repo
    assert by_repo["Qwen3.6-27B"]["is_local_dir"] is True
    assert by_repo["Qwen3.6-27B"]["is_gguf"] is True
