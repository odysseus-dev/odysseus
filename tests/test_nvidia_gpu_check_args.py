import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-docker-gpu.sh"


def test_nvidia_gpu_check_rejects_unknown_extra_arg_before_diagnostics():
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--bad-option"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert "Unknown option: --bad-option" in proc.stderr


def test_nvidia_gpu_check_shell_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_nvidia_gpu_check_allows_cuda_smoke_test_image_override():
    env = dict(os.environ)
    env["ODYSSEUS_DOCKER_GPU_TEST_IMAGE"] = "nvidia/cuda:13.3.0-base-ubuntu24.04"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "--print-install-commands"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0
    assert "nvidia/cuda:13.3.0-base-ubuntu24.04" in proc.stdout
