import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _append_serve_exit_code_lines,
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


def test_serve_runner_preserves_command_exit_code():
    """The serve wrapper must capture `$?` before any echo resets it.

    Otherwise a failed command can be reported as `Process exited with code 0`,
    which hides the real failure from both the UI and diagnosis logic.
    """
    runner_lines = ["vllm serve Qwen/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000"]
    _append_serve_exit_code_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ODYSSEUS_CMD_EXIT=$?" in script
    assert 'echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="' in script
    assert 'echo "=== Process exited with code $? ==="' not in script
