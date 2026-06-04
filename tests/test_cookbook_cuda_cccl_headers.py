"""Regression coverage for split-package CUDA CCCL/CUB headers.

Arch/Fedora-style CUDA installs can put ``cub/cub.cuh`` under an external CCCL
include directory instead of directly under ``CUDA_HOME/include``. The Cookbook
llama.cpp build must bridge that path only when the normal CUDA include tree
does not already expose CUB.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from routes.cookbook_helpers import _append_llama_cpp_linux_accel_build_lines


def _build_script() -> str:
    lines: list[str] = []
    _append_llama_cpp_linux_accel_build_lines(lines)
    return "\n".join(lines)


def _cccl_probe_block() -> str:
    lines = _build_script().splitlines()
    start = next(i for i, line in enumerate(lines) if "_odysseus_cuda_cmake_flags=()" in line)
    end = next(
        i for i, line in enumerate(lines[start:], start)
        if "cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON" in line
    )
    return "\n".join(lines[start:end])


def _write_cub_header(cccl_root: Path) -> None:
    cub_dir = cccl_root / "cub"
    cub_dir.mkdir(parents=True)
    (cub_dir / "cub.cuh").write_text("// fake cub header\n")


def _run_probe(
    *,
    cuda_home: Path,
    search_paths: list[Path] | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    block = _cccl_probe_block()
    if search_paths is not None:
        rebased_paths = " ".join(str(path) for path in search_paths)
        block = re.sub(
            r'_odysseus_cuda_cccl_paths="[^"]+"',
            f"_odysseus_cuda_cccl_paths={shlex.quote(rebased_paths)}",
            block,
        )

    script = (
        "set -e\n"
        f"{block}\n"
        'printf "FLAGS=%s\\n" "${_odysseus_cuda_cmake_flags[*]}"\n'
    )
    env = os.environ.copy()
    env["CUDA_HOME"] = str(cuda_home)
    if extra_env:
        env.update(extra_env)

    out = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    ).stdout
    return next(line for line in out.splitlines() if line.startswith("FLAGS="))[len("FLAGS="):]


def test_cuda_cccl_probe_paths_and_cmake_array_are_generated():
    script = _build_script()

    assert "_odysseus_cuda_cmake_flags=()" in script
    assert "include/cub/cub.cuh" in script
    assert "site-packages/nvidia/cu*/include/cccl" in script
    assert "dist-packages/nvidia/cu*/include/cccl" in script
    assert "/usr/include/cccl" in script
    assert "/usr/local/include/cccl" in script
    assert '"${_odysseus_cuda_cmake_flags[@]}"' in script
    assert script.index("_odysseus_cuda_cmake_flags=()") < script.index("DGGML_CUDA=ON")


def test_cuda_cccl_probe_noops_when_cuda_home_already_has_cub(tmp_path: Path):
    cuda_home = tmp_path / "cuda"
    _write_cub_header(cuda_home / "include")
    split_cccl = tmp_path / "usr/include/cccl"
    _write_cub_header(split_cccl)

    assert _run_probe(cuda_home=cuda_home, search_paths=[split_cccl]) == ""


def test_cuda_cccl_probe_adds_flags_for_cuda_home_cccl_layout(tmp_path: Path):
    cuda_home = tmp_path / "cuda"
    cccl = cuda_home / "include/cccl"
    _write_cub_header(cccl)

    assert _run_probe(cuda_home=cuda_home) == f"-DCMAKE_CUDA_FLAGS=-I{cccl}"


def test_cuda_cccl_probe_finds_split_package_path_and_preserves_existing_flags(tmp_path: Path):
    cuda_home = tmp_path / "cuda"
    (cuda_home / "include").mkdir(parents=True)
    split_cccl = tmp_path / "usr/include/cccl"
    _write_cub_header(split_cccl)

    assert _run_probe(
        cuda_home=cuda_home,
        search_paths=[split_cccl],
        extra_env={"CMAKE_CUDA_FLAGS": "-lineinfo"},
    ) == f"-DCMAKE_CUDA_FLAGS=-lineinfo -I{split_cccl}"
