"""Focused tests for the fixed-purpose native seccomp launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SECCOMP_DIR = ROOT / "security" / "seccomp"
BWRAP = Path("/usr/bin/bwrap")


@pytest.fixture(scope="module")
def test_launcher(tmp_path_factory):
    if shutil.which("make") is None:
        pytest.skip("make is required for launcher tests")
    build_dir = tmp_path_factory.mktemp("launcher-fault-tests")
    completed = subprocess.run(
        [
            "make",
            "-C",
            str(SECCOMP_DIR),
            f"BUILD_DIR={build_dir}",
            "test-launcher",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return build_dir / "odysseus-seccomp-launcher-test"


def _run(launcher: Path, *, stage: str | None = None, arguments=None):
    environment = {}
    if stage is not None:
        environment["ODYSSEUS_TEST_FAIL"] = stage
    bwrap_arguments = [
        str(launcher),
        str(BWRAP),
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
    ]
    if Path("/usr/lib64").exists():
        bwrap_arguments.extend(["--symlink", "usr/lib64", "/lib64"])
    command = [*bwrap_arguments, "--", "/bin/true"]
    if arguments is not None:
        command = [str(launcher), *arguments]
    return subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_launcher_rejects_wrong_bubblewrap_path(test_launcher):
    completed = _run(
        test_launcher,
        arguments=["/bin/true", "--", "/bin/true"],
    )

    assert completed.returncode == 64
    assert completed.stderr.strip().endswith("invalid trusted Bubblewrap path")


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize(
    "option",
    ["--seccomp", "--add-seccomp-fd", "--args"],
)
def test_launcher_rejects_caller_seccomp_options(test_launcher, option):
    completed = _run(
        test_launcher,
        arguments=[str(BWRAP), option, "9", "--", "/bin/true"],
    )

    assert completed.returncode == 65
    assert completed.stderr.strip().endswith("invalid Bubblewrap arguments")


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize(
    ("stage", "exit_code", "message"),
    [
        ("libseccomp", 66, "libseccomp is unavailable or incompatible"),
        ("filter", 67, "inner seccomp filter creation failed"),
        ("memfd", 68, "anonymous filter storage creation failed"),
        ("export", 69, "inner seccomp filter export failed"),
        ("seal", 70, "inner seccomp filter sealing failed"),
        ("exec", 71, "trusted Bubblewrap execution failed"),
    ],
)
def test_launcher_failure_stages_are_distinct_and_concise(
    test_launcher,
    stage,
    exit_code,
    message,
):
    completed = _run(test_launcher, stage=stage)

    assert completed.returncode == exit_code
    assert completed.stdout == ""
    assert completed.stderr.strip() == f"odysseus-seccomp-launcher: {message}"


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_filter_fd_is_sealed_anonymous_memfd(test_launcher):
    completed = _run(test_launcher, stage="inspect")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_launcher_injects_filter_and_executes_payload(test_launcher):
    completed = _run(test_launcher)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_exec_failure_does_not_print_model_command_or_environment(test_launcher):
    secret = "model-command-must-not-be-logged"
    completed = subprocess.run(
        [str(test_launcher), str(BWRAP), "--", "/bin/echo", secret],
        env={"ODYSSEUS_TEST_FAIL": "exec", "API_TOKEN": secret},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 71
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_launcher_elf_has_expected_hardening(test_launcher):
    if shutil.which("readelf") is None:
        pytest.skip("readelf is required for ELF hardening assertions")
    header = subprocess.run(
        ["readelf", "-h", str(test_launcher)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    program = subprocess.run(
        ["readelf", "-W", "-l", str(test_launcher)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    dynamic = subprocess.run(
        ["readelf", "-d", str(test_launcher)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "DYN (Position-Independent Executable file)" in header
    assert "GNU_RELRO" in program
    assert "GNU_STACK" in program
    assert "BIND_NOW" in dynamic
    assert os.access(test_launcher, os.X_OK)
