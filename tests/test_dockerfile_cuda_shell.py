"""Regression tests for Dockerfile CUDA setup shell compatibility."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def _cuda_setup_run_script() -> str:
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("RUN _cuda_opt="):
            block = [line.removeprefix("RUN ")]
            while block[-1].rstrip().endswith("\\"):
                index += 1
                block.append(lines[index])
            return "\n".join(block)
    raise AssertionError("CUDA setup RUN layer not found in Dockerfile")


def test_cuda_setup_run_parses_under_posix_sh():
    result = subprocess.run(
        ["/bin/sh", "-n", "-c", _cuda_setup_run_script()],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_cuda_setup_run_avoids_bash_only_constructs():
    script = _cuda_setup_run_script()

    assert "^^}" not in script
    assert ",,}" not in script
    assert "_cuda_pkgs=(" not in script
    assert "${_cuda_pkgs[@" not in script
