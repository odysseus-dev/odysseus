import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
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


# TODO(cross-platform): the two tests below FAIL on Windows (pre-existing,
# unrelated to the ai_interaction refactor). Root cause: _local_tooling_path_export
# in routes/cookbook_helpers.py builds the bash PATH line with os.path.abspath/
# dirname, which are OS-dependent. On Windows a POSIX input like
# "/opt/venv/bin/python" becomes "C:\opt\venv\bin" (drive letter + backslashes),
# so the produced line is  export PATH="C:\\opt\\venv\\bin:$PATH"  instead of the
# expected  export PATH="/opt/venv/bin:$PATH".  These exports are bash-only
# ("Local runs only", consumed by tmux/login shells on Linux/macOS), so the path
# math should be POSIX regardless of host OS.
# Fix: in _local_tooling_path_export use posixpath instead of os.path, e.g.
#   bin_dir = posixpath.dirname(executable)  # keep forward slashes, no drive letter
# (and drop abspath, which only injects a Windows drive). Then these tests pass on
# every OS. Alternatively, gate the tests with
# @pytest.mark.skipif(os.name == "nt", reason="bash PATH export is POSIX-only").
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
