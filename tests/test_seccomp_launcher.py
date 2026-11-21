"""Focused tests for the fixed-purpose native seccomp launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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


@pytest.fixture(scope="module")
def seccomp_probe(tmp_path_factory):
    if shutil.which("cc") is None:
        pytest.skip("a C compiler is required for seccomp probe tests")
    build_dir = tmp_path_factory.mktemp("seccomp-probe")
    probe = build_dir / "seccomp-probe"
    completed = subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(ROOT / "tests" / "seccomp_probe.c"),
            "-o",
            str(probe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return probe


def _base_arguments(payload: list[str] | None = None) -> list[str]:
    arguments = [
        str(BWRAP),
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--cap-drop",
        "ALL",
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
        arguments.extend(["--symlink", "usr/lib64", "/lib64"])
    arguments.extend(
        [
            "--dev",
            "/dev",
            "--ro-bind",
            "/proc",
            "/proc",
            "--bind",
            str(ROOT),
            str(ROOT),
            "--chdir",
            str(ROOT),
            "--",
            *(payload or ["/bin/true"]),
        ]
    )
    return arguments


def _with_option(arguments: list[str], *option: str) -> list[str]:
    updated = list(arguments)
    updated[updated.index("--"):updated.index("--")] = option
    return updated


def _with_fresh_proc(arguments: list[str]) -> list[str]:
    updated = list(arguments)
    for index in range(len(updated) - 2):
        if updated[index:index + 3] == ["--ro-bind", "/proc", "/proc"]:
            updated[index:index + 3] = ["--proc", "/proc"]
            return updated
    raise AssertionError("test profile has no read-only proc mount")


def _run(
    launcher: Path,
    *,
    stage: str | None = None,
    arguments=None,
    environment: dict[str, str] | None = None,
):
    child_environment = dict(environment or {})
    if stage is not None:
        child_environment["ODYSSEUS_TEST_FAIL"] = stage
    command = [str(launcher), *(arguments or _base_arguments())]
    return subprocess.run(
        command,
        env=child_environment,
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
    ["--seccomp", "--add-seccomp-fd", "--args", "--share-net", "--dev-bind"],
)
def test_launcher_rejects_options_outside_fixed_contract(test_launcher, option):
    completed = _run(
        test_launcher,
        arguments=_with_option(_base_arguments(), option, "9"),
    )

    assert completed.returncode == 65
    assert completed.stderr.strip().endswith("invalid Bubblewrap arguments")


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize(
    "option",
    [
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--new-session",
    ],
)
def test_launcher_requires_every_fixed_isolation_option(test_launcher, option):
    arguments = _base_arguments()
    arguments.remove(option)

    completed = _run(test_launcher, arguments=arguments)

    assert completed.returncode == 65
    assert completed.stderr.strip().endswith("invalid Bubblewrap arguments")


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize(
    "option",
    [
        ("--bind", "/etc", "/etc"),
        ("--ro-bind", str(BWRAP), "/tmp/bwrap-copy"),
        ("--setenv", "API_TOKEN", "caller-secret"),
    ],
)
def test_launcher_rejects_escape_mounts_and_environment(test_launcher, option):
    completed = _run(
        test_launcher,
        arguments=_with_option(_base_arguments(), *option),
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
def test_launcher_accepts_the_production_fresh_proc_contract(test_launcher):
    completed = _run(
        test_launcher,
        stage="inspect",
        arguments=_with_fresh_proc(_base_arguments()),
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_launcher_accepts_only_the_explicit_full_access_mount_shape(test_launcher):
    arguments = _with_fresh_proc(_base_arguments())
    usr_bind = arguments.index("--ro-bind")
    del arguments[usr_bind:usr_bind + 3]
    dev_mount = arguments.index("--dev")
    del arguments[dev_mount:dev_mount + 2]
    while "--symlink" in arguments:
        symlink = arguments.index("--symlink")
        del arguments[symlink:symlink + 3]
    workspace_bind = arguments.index("--bind")
    arguments[workspace_bind + 1:workspace_bind + 3] = ["/", "/"]

    completed = _run(test_launcher, stage="inspect", arguments=arguments)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_launcher_accepts_the_brokered_egress_runtime_mount(test_launcher):
    with tempfile.TemporaryDirectory(prefix="odysseus-egress-", dir="/tmp") as runtime:
        arguments = _with_option(
            _base_arguments(),
            "--dir",
            "/run",
            "--ro-bind",
            runtime,
            "/run/odysseus-egress",
        )

        completed = _run(test_launcher, stage="inspect", arguments=arguments)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_launcher_accepts_one_trusted_native_venv_mount(test_launcher, tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(
        "include-system-site-packages = false\n",
        encoding="utf-8",
    )
    (venv / "bin" / "python").symlink_to(os.path.realpath(os.sys.executable))
    arguments = _with_option(
        _base_arguments(["/run/odysseus-python-venv/bin/python", "-I", "-c", "pass"]),
        "--dir",
        "/run/odysseus-python-venv",
        "--ro-bind",
        str(venv),
        "/run/odysseus-python-venv",
    )

    completed = _run(test_launcher, stage="inspect", arguments=arguments)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize(
    "option",
    [
        ("--ro-bind", str(ROOT), "/run/odysseus-egress"),
        ("--ro-bind", str(ROOT), "/run/odysseus-python-venv"),
    ],
)
def test_launcher_rejects_lookalike_runtime_mounts(test_launcher, option):
    completed = _run(
        test_launcher,
        stage="inspect",
        arguments=_with_option(_base_arguments(), *option),
    )

    assert completed.returncode == 65
    assert completed.stderr.strip().endswith("invalid Bubblewrap arguments")


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize("probe_name", ["tiocsti", "tiocsti_high_bits"])
def test_tiocsti_low_bits_are_denied_after_filter_load(
    test_launcher,
    seccomp_probe,
    probe_name,
):
    arguments = _with_option(
        _base_arguments(["/run/odysseus/command.sh", probe_name]),
        "--dir",
        "/run/odysseus",
        "--ro-bind",
        str(seccomp_probe),
        "/run/odysseus/command.sh",
    )
    completed = _run(
        test_launcher,
        arguments=arguments,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
@pytest.mark.parametrize(
    "probe_name",
    [
        "bpf",
        "perf_event_open",
        "clone_namespace",
        "clone3",
        "unshare",
        "setns",
        "mount",
        "umount2",
        "pivot_root",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "keyctl",
        "open_by_handle_at",
        "af_packet",
        "af_alg",
        "af_vsock",
        "userfaultfd",
        "io_uring_setup",
        "clone_process",
        "socket_unix",
        "socket_inet",
        "socket_inet6",
        "socketpair_unix",
        "socketpair_inet_denied",
        "personality_query",
        "personality_denied",
        "direct_bwrap",
    ],
)
def test_dangerous_and_argument_sensitive_syscall_matrix(
    test_launcher,
    seccomp_probe,
    probe_name,
):
    arguments = _with_option(
        _base_arguments(["/run/odysseus/command.sh", probe_name]),
        "--dir",
        "/run/odysseus",
        "--ro-bind",
        str(seccomp_probe),
        "/run/odysseus/command.sh",
    )

    completed = _run(test_launcher, arguments=arguments)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_launcher_scrubs_parent_environment_and_allows_only_safe_explicit_names(
    test_launcher,
):
    secret = "must-not-reach-bubblewrap-or-payload"
    arguments = _with_option(
        _base_arguments(["/usr/bin/env"]),
        "--setenv",
        "TERM",
        "xterm-256color",
    )
    arguments.remove("--clearenv")

    completed = _run(
        test_launcher,
        arguments=arguments,
        environment={"API_TOKEN": secret, "HOME": f"/tmp/{secret}"},
    )

    assert completed.returncode == 0, completed.stderr
    payload_environment = dict(
        line.split("=", 1) for line in completed.stdout.splitlines()
    )
    assert payload_environment == {"PWD": str(ROOT), "TERM": "xterm-256color"}
    assert secret not in completed.stderr


@pytest.mark.skipif(not BWRAP.is_file(), reason="canonical Bubblewrap is unavailable")
def test_exec_failure_does_not_print_model_command_or_environment(test_launcher):
    secret = "model-command-must-not-be-logged"
    arguments = _base_arguments(["/bin/echo", secret])
    completed = subprocess.run(
        [str(test_launcher), *arguments],
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
