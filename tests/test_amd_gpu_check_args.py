import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-docker-amd-gpu.sh"


def test_amd_gpu_check_rejects_unknown_extra_arg_before_diagnostics():
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--bad-option"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert "Unknown option: --bad-option" in proc.stderr


def test_amd_gpu_check_shell_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
