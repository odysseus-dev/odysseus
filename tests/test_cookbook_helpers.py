import json
import subprocess
import sys

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _cached_model_scan_script,
    _local_tooling_path_export,
    _safe_env_prefix,
    _validate_gpus,
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
    out = {"models": json.loads(proc.stdout)}

    by_repo = {m["repo_id"]: m for m in out["models"]}
    assert "models--Qwen--Qwen3.6-27B" not in by_repo
    assert by_repo["Qwen3.6-27B"]["is_local_dir"] is True
    assert by_repo["Qwen3.6-27B"]["is_gguf"] is True
