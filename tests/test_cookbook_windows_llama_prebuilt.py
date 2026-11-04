"""Local Windows serves fetch a prebuilt llama-server when none is on PATH.

Regression for #5177: the local Windows runner calls the native
``llama-server`` but skipped every bootstrap step, so a fresh install died with
``llama-server: command not found`` (exit 127). The runner now downloads the
matching Windows build from llama.cpp's releases. These tests drive the
generated bash with ``curl`` / ``nvidia-smi`` / ``powershell.exe`` stubbed out,
so nothing touches the network.
"""
import os
import subprocess
import zipfile

import pytest

from core.platform_compat import IS_WINDOWS, find_bash, git_bash_path
from routes.cookbook_helpers import (
    WINDOWS_LLAMA_PREBUILT_DIR,
    _append_llama_cpp_windows_prebuilt_lines,
)

RELEASE = "https://github.com/ggml-org/llama.cpp/releases/download/b9999"
ASSETS = [
    f"{RELEASE}/cudart-llama-bin-win-cuda-13.1-x64.zip",
    f"{RELEASE}/cudart-llama-bin-win-cuda-12.4-x64.zip",
    f"{RELEASE}/llama-b9999-bin-win-cuda-13.1-x64.zip",
    f"{RELEASE}/llama-b9999-bin-win-cuda-12.4-x64.zip",
    f"{RELEASE}/llama-b9999-bin-win-vulkan-x64.zip",
    f"{RELEASE}/llama-b9999-bin-win-cpu-x64.zip",
    f"{RELEASE}/llama-b9999-bin-win-cpu-arm64.zip",
    f"{RELEASE}/llama-b9999-bin-ubuntu-x64.zip",
]


def _run_bootstrap(tmp_path, *, nvidia=False, gpu_name="Intel(R) UHD Graphics", stable_without_binaries=False):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    with zipfile.ZipFile(fixtures / "llama.zip", "w") as z:
        z.writestr("llama-server.exe", "MZ")
        z.writestr("ggml-base.dll", "dll")
    with zipfile.ZipFile(fixtures / "cudart.zip", "w") as z:
        z.writestr("cudart64_12.dll", "dll")

    listing = "\n".join(f'    "browser_download_url": "{url}",' for url in ASSETS)
    stubs = [
        "curl() {",
        '  local out="" url=""',
        '  while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; -*) shift;; *) url="$1"; shift;; esac; done',
        '  case "$url" in',
        f"    *api.github.com*) cat <<'JSON'\n{listing}\nJSON\n    ;;",
        f'    *cudart-*) echo "GET $url"; cp "{git_bash_path(fixtures / "cudart.zip")}" "$out";;',
        f'    *) echo "GET $url"; cp "{git_bash_path(fixtures / "llama.zip")}" "$out";;',
        "  esac",
        "}",
        f'powershell.exe() {{ echo "{gpu_name}"; }}',
        "nvidia-smi() { return 1; }",
    ]
    if nvidia:
        stubs.append('nvidia-smi() { echo "GPU 0: NVIDIA GeForce RTX 4060 Ti (UUID: GPU-x)"; }')
    if stable_without_binaries:
        stubs.insert(3, '  case "$url" in *releases/latest*) echo \'{"assets":[{"browser_download_url":"https://github.com/ggml-org/llama.cpp/releases/download/v0.8.0/nightly-tag.txt"}]}\'; return;; esac')
    lines = []
    _append_llama_cpp_windows_prebuilt_lines(lines)

    env = dict(os.environ)
    env["HOME"] = git_bash_path(tmp_path / "home")
    env["PROCESSOR_ARCHITECTURE"] = "AMD64"
    env.pop("PROCESSOR_ARCHITEW6432", None)
    (tmp_path / "home").mkdir()
    result = subprocess.run(
        [find_bash() or "bash", "-c", "\n".join(stubs + lines)],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    install_dir = tmp_path / "home" / WINDOWS_LLAMA_PREBUILT_DIR.replace("$HOME/", "")
    return result.stdout, install_dir


def _downloads(stdout):
    return [line[len("GET "):] for line in stdout.splitlines() if line.startswith("GET ")]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"nvidia": True},
            [f"{RELEASE}/llama-b9999-bin-win-cuda-12.4-x64.zip",
             f"{RELEASE}/cudart-llama-bin-win-cuda-12.4-x64.zip"],
        ),
        ({"gpu_name": "AMD Radeon RX 7900 XTX"}, [f"{RELEASE}/llama-b9999-bin-win-vulkan-x64.zip"]),
        ({}, [f"{RELEASE}/llama-b9999-bin-win-cpu-x64.zip"]),
    ],
    ids=["nvidia-cuda12-with-cudart", "amd-vulkan", "cpu"],
)
def test_bootstrap_picks_the_windows_build_for_the_gpu(tmp_path, kwargs, expected):
    stdout, install_dir = _run_bootstrap(tmp_path, **kwargs)

    assert _downloads(stdout) == expected
    assert (install_dir / "llama-server.exe").is_file()
    assert (install_dir / "ggml-base.dll").is_file()
    assert (install_dir / "cudart64_12.dll").is_file() == bool(kwargs.get("nvidia"))
    # The staging directory is cleaned up after the move.
    assert sorted(p.name for p in install_dir.parent.iterdir()) == [install_dir.name]


@pytest.mark.skipif(not IS_WINDOWS, reason="Git Bash resolves llama-server to llama-server.exe only on Windows")
def test_installed_llama_server_resolves_and_is_not_fetched_again(tmp_path):
    stdout, _ = _run_bootstrap(tmp_path)
    assert "Prebuilt llama-server installed" in stdout
    assert "Could not install llama-server" not in stdout

    lines = []
    _append_llama_cpp_windows_prebuilt_lines(lines)
    env = dict(os.environ, HOME=git_bash_path(tmp_path / "home"))
    second = subprocess.run(
        [find_bash() or "bash", "-c", "\n".join(lines + ["command -v llama-server"])],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert second.returncode == 0, second.stderr
    assert "fetching" not in second.stdout
    assert second.stdout.strip().endswith("/llama-server")


def test_local_windows_runner_bootstraps_native_llama_server():
    from pathlib import Path

    routes = (Path(__file__).resolve().parent.parent / "routes/cookbook_routes.py").read_text(encoding="utf-8")
    assert 'if local_windows_llama_cmd and "llama-server" in req.cmd:' in routes
    assert "_append_llama_cpp_windows_prebuilt_lines(runner_lines)" in routes


def test_bootstrap_uses_prerelease_assets_when_latest_has_only_a_tag_pointer(tmp_path):
    stdout, install_dir = _run_bootstrap(tmp_path, stable_without_binaries=True)
    assert _downloads(stdout) == [f"{RELEASE}/llama-b9999-bin-win-cpu-x64.zip"]
    assert (install_dir / "llama-server.exe").is_file()
