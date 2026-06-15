import subprocess
from pathlib import Path

from core.platform_compat import find_bash, git_bash_path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-docker-amd-gpu.sh"


def test_amd_gpu_check_rejects_unknown_extra_arg_before_diagnostics():
    bash = find_bash() or "bash"
    proc = subprocess.run(
        [bash, git_bash_path(SCRIPT), "--bad-option"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert "Unknown option: --bad-option" in proc.stderr


def test_amd_gpu_check_shell_syntax():
    bash = find_bash() or "bash"
    subprocess.run([bash, "-n", git_bash_path(SCRIPT)], check=True)
