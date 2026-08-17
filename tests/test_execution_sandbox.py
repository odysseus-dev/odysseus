"""Linux sandbox invariants for model-requested process execution."""

import asyncio
import os
import socket
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from src.execution_sandbox import (
    SandboxNetworkProfile,
    SandboxUnavailable,
    environment_for_sandbox_launcher,
    sandbox_command,
)


@pytest.fixture(autouse=True)
def _stable_bubblewrap_lookup(monkeypatch):
    """Keep argv-only tests independent of the CI runner's package set."""
    monkeypatch.setattr(
        "src.execution_sandbox._bubblewrap_binary",
        lambda: "/usr/bin/bwrap",
    )
    monkeypatch.setattr(
        "src.execution_sandbox._seccomp_launcher_binary",
        lambda: "/usr/local/libexec/odysseus-seccomp-launcher",
    )
    monkeypatch.setattr(
        "src.execution_sandbox._egress_broker_binary",
        lambda: "/usr/local/libexec/odysseus-egress-broker",
    )
    monkeypatch.setattr(
        "src.execution_sandbox._egress_bridge_binary",
        lambda: "/usr/local/libexec/odysseus-egress-bridge",
    )


requires_bubblewrap = pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("make") is None,
    reason="bubblewrap and make are required for sandbox runtime assertions",
)


@pytest.fixture(scope="session")
def compiled_seccomp_launcher(tmp_path_factory):
    build_dir = tmp_path_factory.mktemp("seccomp-launcher")
    source_dir = Path(__file__).resolve().parents[1] / "security" / "seccomp"
    completed = subprocess.run(
        [
            "make",
            "-C",
            str(source_dir),
            f"BUILD_DIR={build_dir}",
            "all",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"trusted launcher compilation failed: {completed.stderr}")
    return build_dir / "odysseus-seccomp-launcher"


@pytest.fixture
def runtime_seccomp_launcher(monkeypatch, compiled_seccomp_launcher, tmp_path):
    monkeypatch.setattr(
        "src.execution_sandbox._seccomp_launcher_binary",
        lambda: str(compiled_seccomp_launcher),
    )
    preflight_workspace = tmp_path / "sandbox-preflight"
    preflight_workspace.mkdir()
    completed = subprocess.run(
        sandbox_command(["/bin/true"], workspace=str(preflight_workspace)),
        cwd=preflight_workspace,
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if (
        completed.returncode != 0
        and "Can't mount proc" in completed.stderr
        and "Operation not permitted" in completed.stderr
    ):
        pytest.skip(
            "secretless runner outer sandbox blocks fresh procfs; "
            "shipped outer OCI profile validation is required"
        )
    assert completed.returncode == 0, completed.stderr
    return compiled_seccomp_launcher


def test_sandbox_argv_is_positive_mount_networkless_by_default_and_clearenv(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    argv = sandbox_command(["/bin/bash", "-c", "true"], workspace=str(workspace))

    assert argv[:2] == [
        "/usr/local/libexec/odysseus-seccomp-launcher",
        "/usr/bin/bwrap",
    ]
    assert "/usr/local/libexec/odysseus-egress-broker" not in argv
    assert "/usr/local/libexec/odysseus-egress-bridge" not in argv
    assert "/run/odysseus-egress/broker.sock" not in argv
    for option in (
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-cgroup",
    ):
        assert option in argv
    assert "--share-net" not in argv
    assert "--seccomp" not in argv
    assert ["--proc", "/proc"] in [
        argv[index:index + 2] for index in range(len(argv) - 1)
    ]
    assert "--clearenv" in argv
    assert "/usr/bin/prlimit" in argv
    assert "--nproc=256" in argv
    assert "--as=4294967296" in argv
    assert ["--ro-bind", "/", "/"] not in [
        argv[index:index + 3] for index in range(len(argv) - 2)
    ]
    bind_index = argv.index("--bind")
    assert argv[bind_index + 1:bind_index + 3] == [
        str(workspace),
        str(workspace),
    ]
    assert environment_for_sandbox_launcher() == {}
    assert "OPENAI_API_KEY" not in argv
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "no_proxy"):
        assert variable not in argv


def test_trusted_executable_rejects_missing_or_writable_install(monkeypatch):
    from src.execution_sandbox import _trusted_executable

    with pytest.raises(SandboxUnavailable, match="requires the trusted"):
        _trusted_executable("/definitely/missing/launcher", "seccomp launcher")

    metadata = type(
        "Metadata",
        (),
        {"st_mode": stat.S_IFREG | 0o775, "st_uid": 0},
    )()
    monkeypatch.setattr("src.execution_sandbox.os.stat", lambda _path: metadata)
    monkeypatch.setattr("src.execution_sandbox.os.path.realpath", lambda path: path)
    monkeypatch.setattr("src.execution_sandbox.os.access", lambda *_args: True)
    with pytest.raises(
        SandboxUnavailable,
        match="root-owned, non-setuid, read-only",
    ):
        _trusted_executable("/trusted/launcher", "seccomp launcher")

    metadata.st_mode = stat.S_IFREG | stat.S_ISUID | 0o755
    with pytest.raises(SandboxUnavailable, match="non-setuid"):
        _trusted_executable("/trusted/launcher", "seccomp launcher")


def test_trusted_python_helpers_require_fixed_isolated_interpreter(
    tmp_path,
    monkeypatch,
):
    from src.execution_sandbox import _trusted_python_helper

    helper = tmp_path / "broker"
    helper.write_text("#!/usr/bin/python3.13 -I\n", encoding="ascii")
    metadata = type(
        "Metadata",
        (),
        {"st_mode": stat.S_IFREG | 0o755, "st_uid": 0},
    )()
    monkeypatch.setattr("src.execution_sandbox.os.stat", lambda _path: metadata)
    monkeypatch.setattr("src.execution_sandbox.os.path.realpath", lambda path: path)
    monkeypatch.setattr("src.execution_sandbox.os.access", lambda *_args: True)

    assert _trusted_python_helper(str(helper), "egress broker") == str(helper)

    helper.write_text("#!/usr/bin/env python3\n", encoding="ascii")
    with pytest.raises(SandboxUnavailable, match="isolated absolute"):
        _trusted_python_helper(str(helper), "egress broker")


def test_sandbox_rejects_invalid_network_profile(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(SandboxUnavailable, match="Invalid server-owned"):
        sandbox_command(
            ["/bin/true"],
            workspace=str(workspace),
            network_profile="open",  # type: ignore[arg-type]
        )


def test_sandbox_rejects_launcher_workspace_overlap(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "src.execution_sandbox._seccomp_launcher_binary",
        lambda: str(workspace / "odysseus-seccomp-launcher"),
    )

    with pytest.raises(SandboxUnavailable, match="overlaps"):
        sandbox_command(["/bin/true"], workspace=str(workspace))


def test_brokered_profile_uses_private_namespace_and_trusted_proxy(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    argv = sandbox_command(
        ["/bin/bash", "-c", "true"],
        workspace=str(workspace),
        network_profile=SandboxNetworkProfile.BROKERED_ONLY,
    )

    assert argv[:3] == [
        "/usr/local/libexec/odysseus-egress-broker",
        "/usr/local/libexec/odysseus-seccomp-launcher",
        "/usr/bin/bwrap",
    ]
    assert "--unshare-net" in argv
    assert "--share-net" not in argv
    separator = argv.index("--")
    assert argv[separator + 1:separator + 4] == [
        "/usr/local/libexec/odysseus-egress-bridge",
        "/run/odysseus-egress/broker.sock",
        "--",
    ]
    environment = {
        argv[index + 1]: argv[index + 2]
        for index, value in enumerate(argv[:-2])
        if value == "--setenv"
    }
    assert environment["HTTP_PROXY"] == "http://127.0.0.1:3128"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert environment["http_proxy"] == "http://127.0.0.1:3128"
    assert environment["https_proxy"] == "http://127.0.0.1:3128"
    assert "NO_PROXY" not in environment
    assert "no_proxy" not in environment
    assert "ALL_PROXY" not in environment
    assert all(
        "@" not in environment[name]
        for name in environment
        if "proxy" in name.lower()
    )
    assert "OPENAI_API_KEY" not in argv


def test_brokered_profile_fails_closed_when_trusted_broker_is_missing(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def missing_broker():
        raise SandboxUnavailable(
            "Sandboxed agent execution requires the trusted egress broker."
        )

    monkeypatch.setattr(
        "src.execution_sandbox._egress_broker_binary",
        missing_broker,
    )
    with pytest.raises(SandboxUnavailable, match="trusted egress broker"):
        sandbox_command(
            ["/bin/true"],
            workspace=str(workspace),
            network_profile=SandboxNetworkProfile.BROKERED_ONLY,
        )


def test_brokered_profile_fails_closed_without_ca_bundle(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "src.execution_sandbox._CA_CERTIFICATE",
        "/definitely/missing/ca-certificates.crt",
    )

    with pytest.raises(SandboxUnavailable, match="CA certificate bundle"):
        sandbox_command(
            ["/bin/true"],
            workspace=str(workspace),
            network_profile=SandboxNetworkProfile.BROKERED_ONLY,
        )


def test_sandbox_overlays_credentials_and_protects_git(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".ssh").mkdir()
    (workspace / ".config" / "gh").mkdir(parents=True)
    (workspace / ".profile").write_text("persist", encoding="utf-8")

    argv = sandbox_command(["/bin/true"], workspace=str(workspace))

    triples = [argv[index:index + 3] for index in range(len(argv) - 2)]
    pairs = [argv[index:index + 2] for index in range(len(argv) - 1)]
    assert ["--ro-bind", "/dev/null", str(workspace / ".env")] in triples
    assert [
        "--ro-bind",
        str(workspace / ".git"),
        str(workspace / ".git"),
    ] in triples
    assert ["--tmpfs", str(workspace / ".ssh")] in pairs
    assert ["--tmpfs", str(workspace / ".config" / "gh")] in pairs
    assert ["--ro-bind", "/dev/null", str(workspace / ".profile")] in triples


def test_sandbox_rejects_preexisting_unix_socket_in_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_path = workspace / "docker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    try:
        with pytest.raises(SandboxUnavailable, match="unsupported special file"):
            sandbox_command(["/bin/true"], workspace=str(workspace))
    finally:
        listener.close()


def test_sandbox_rejects_nested_mount_in_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "mounted-host-tree"
    nested.mkdir()
    mountinfo = (
        "36 25 0:32 / / rw,relatime - overlay overlay rw\n"
        f"37 36 0:33 / {nested} rw,relatime - tmpfs tmpfs rw\n"
    )
    monkeypatch.setattr(
        "src.execution_sandbox._MOUNTINFO_PATH",
        str(tmp_path / "mountinfo"),
    )
    (tmp_path / "mountinfo").write_text(mountinfo, encoding="utf-8")

    with pytest.raises(SandboxUnavailable, match="nested mount"):
        sandbox_command(["/bin/true"], workspace=str(workspace))


def test_sandbox_rejects_bind_mounted_regular_file(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace with space"
    workspace.mkdir()
    mounted_file = workspace / "innocent.txt"
    mounted_file.write_text("external", encoding="utf-8")
    escaped_mount = str(mounted_file).replace(" ", r"\040")
    mountinfo = (
        "36 25 0:32 / / rw,relatime - overlay overlay rw\n"
        f"37 36 0:33 / {escaped_mount} rw,relatime - ext4 /dev/root rw\n"
    )
    mountinfo_path = tmp_path / "mountinfo"
    mountinfo_path.write_text(mountinfo, encoding="utf-8")
    monkeypatch.setattr(
        "src.execution_sandbox._MOUNTINFO_PATH",
        str(mountinfo_path),
    )

    with pytest.raises(SandboxUnavailable, match="nested mount"):
        sandbox_command(["/bin/true"], workspace=str(workspace))


def test_sandbox_fails_closed_when_mountinfo_is_unavailable(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "src.execution_sandbox._MOUNTINFO_PATH",
        str(tmp_path / "missing-mountinfo"),
    )

    with pytest.raises(SandboxUnavailable, match="mount boundaries"):
        sandbox_command(["/bin/true"], workspace=str(workspace))


def test_sandbox_rejects_broad_workspace():
    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace="/")

    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace="/usr/local/share/agent")


def test_sandbox_rejects_the_process_home_as_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace=str(tmp_path))


def test_sandbox_protects_worktree_git_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git_file = workspace / ".git"
    git_file.write_text("gitdir: /outside", encoding="utf-8")

    argv = sandbox_command(["/bin/true"], workspace=str(workspace))

    triples = [argv[index:index + 3] for index in range(len(argv) - 2)]
    assert ["--ro-bind", str(git_file), str(git_file)] in triples


def test_sandbox_rejects_symlinked_sensitive_mounts(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".git").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace=str(workspace))


@requires_bubblewrap
def test_sandbox_hides_odysseus_data_inside_broader_workspace(
    tmp_path,
    monkeypatch,
    runtime_seccomp_launcher,
):
    import src.constants as constants

    workspace = tmp_path / "app"
    data_dir = workspace / "data"
    logs_dir = workspace / "logs"
    agent_dir = data_dir / "agent_workspace"
    data_dir.mkdir(parents=True)
    logs_dir.mkdir()
    agent_dir.mkdir()
    (workspace / "allowed.txt").write_text("workspace", encoding="utf-8")
    (data_dir / "app.db").write_text("private", encoding="utf-8")
    (data_dir / ".env").write_text("PRIVATE=value", encoding="utf-8")
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(constants, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(constants, "AGENT_WORKSPACE_DIR", str(agent_dir))
    monkeypatch.setattr(constants, "MAIL_ATTACHMENTS_DIR", str(data_dir / "mail"))

    argv = sandbox_command(
        [
            "/bin/bash",
            "-c",
            "test -s allowed.txt && test ! -e data/app.db && test ! -e logs/private.log",
        ],
        workspace=str(workspace),
    )

    pairs = [argv[index:index + 2] for index in range(len(argv) - 1)]
    assert ["--tmpfs", str(data_dir)] in pairs
    assert ["--tmpfs", str(logs_dir)] in pairs
    completed = subprocess.run(
        argv,
        cwd=str(workspace),
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_sandbox_masks_configured_sqlite_database_inside_workspace(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = workspace / "custom.db"
    database.write_text("private", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    argv = sandbox_command(["/bin/true"], workspace=str(workspace))

    triples = [argv[index:index + 3] for index in range(len(argv) - 2)]
    assert ["--ro-bind", "/dev/null", str(database)] in triples


def test_sandbox_resolves_relative_configured_sqlite_database(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = workspace / "relative.db"
    database.write_text("private", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///relative.db")
    monkeypatch.setattr("src.runtime_paths.get_app_root", lambda: str(workspace))

    argv = sandbox_command(["/bin/true"], workspace=str(workspace))

    triples = [argv[index:index + 3] for index in range(len(argv) - 2)]
    assert ["--ro-bind", "/dev/null", str(database)] in triples


def test_sandbox_allows_only_dedicated_workspace_below_data(
    tmp_path,
    monkeypatch,
):
    import src.constants as constants

    data_dir = tmp_path / "data"
    agent_dir = data_dir / "agent_workspace"
    private_dir = data_dir / "personal_docs"
    agent_dir.mkdir(parents=True)
    private_dir.mkdir()
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(constants, "LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(constants, "AGENT_WORKSPACE_DIR", str(agent_dir))
    monkeypatch.setattr(constants, "MAIL_ATTACHMENTS_DIR", str(data_dir / "mail"))

    assert sandbox_command(["/bin/true"], workspace=str(agent_dir))
    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace=str(private_dir))


@requires_bubblewrap
def test_sandbox_hides_host_and_environment_at_runtime(
    tmp_path,
    runtime_seccomp_launcher,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("outside", encoding="utf-8")
    (workspace / ".env").write_text("INSIDE_SECRET=value", encoding="utf-8")
    (workspace / ".git").mkdir()
    command = (
        "set -eu; "
        "test ! -e \"$1\"; "
        "test -z \"${OPENAI_API_KEY:-}\"; "
        "test ! -s .env; "
        "test ! -e /home; "
        "test -r /proc/self/status; "
        "touch allowed.txt; "
        "if touch .git/blocked 2>/dev/null; then exit 91; fi"
    )
    argv = sandbox_command(
        ["/bin/bash", "-c", command, "sandbox", str(outside)],
        workspace=str(workspace),
    )
    env = {"OPENAI_API_KEY": "must-not-cross"}

    completed = subprocess.run(
        argv,
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (workspace / "allowed.txt").exists()
    assert not (workspace / ".git" / "blocked").exists()


@requires_bubblewrap
def test_sandbox_network_namespace_has_no_external_route(
    tmp_path,
    runtime_seccomp_launcher,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        code = (
            "import socket; "
            "s=socket.socket(); s.settimeout(0.2); "
            f"\ntry: s.connect(('127.0.0.1', {port}))"
            "\nexcept OSError: raise SystemExit(0)"
            "\nraise SystemExit(1)"
        )
        argv = sandbox_command(
            ["/usr/bin/python3", "-I", "-c", code],
            workspace=str(workspace),
        )

        completed = subprocess.run(
            argv,
            cwd=str(workspace),
            env={},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr


@requires_bubblewrap
def test_sandbox_status_has_one_additional_inner_filter(
    tmp_path,
    runtime_seccomp_launcher,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def status_value(text, name):
        for line in text.splitlines():
            if line.startswith(f"{name}:"):
                return int(line.split(":", 1)[1].strip())
        raise AssertionError(f"missing {name} in process status")

    parent_status = Path("/proc/self/status").read_text(encoding="utf-8")
    parent_filters = status_value(parent_status, "Seccomp_filters")
    argv = sandbox_command(
        [
            "/bin/bash",
            "-c",
            "grep -E '^(NoNewPrivs|Seccomp|Seccomp_filters):' /proc/self/status",
        ],
        workspace=str(workspace),
    )
    completed = subprocess.run(
        argv,
        cwd=workspace,
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert status_value(completed.stdout, "NoNewPrivs") == 1
    assert status_value(completed.stdout, "Seccomp") == 2
    assert status_value(completed.stdout, "Seccomp_filters") == parent_filters + 1


@requires_bubblewrap
@pytest.mark.parametrize(
    "probe",
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
        "tiocsti",
        "tiocsti_high_bits",
        "userfaultfd",
        "io_uring_setup",
        "fork",
    ],
)
def test_inner_seccomp_syscall_policy(
    tmp_path,
    runtime_seccomp_launcher,
    probe,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    probe_source = Path(__file__).with_name("seccomp_probe.c")
    probe_binary = workspace / "seccomp-probe"
    compiled = subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(probe_source),
            "-o",
            str(probe_binary),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    completed = subprocess.run(
        sandbox_command([str(probe_binary), probe], workspace=str(workspace)),
        cwd=workspace,
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    if completed.returncode == 77:
        pytest.skip(f"{probe} is unavailable on this architecture")
    assert completed.returncode == 0, completed.stderr


@requires_bubblewrap
def test_common_development_workloads_remain_compatible(
    tmp_path,
    runtime_seccomp_launcher,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = r"""
set -eu
printf 'alpha\n' > ordinary.txt
cp ordinary.txt renamed.txt
rm ordinary.txt
python3 - <<'PY'
import multiprocessing
import pathlib
import subprocess
import threading

seen = []
thread = threading.Thread(target=lambda: seen.append("thread"))
thread.start()
thread.join()
assert seen == ["thread"]
assert subprocess.check_output(["/bin/sh", "-c", "printf child"]) == b"child"
proc = multiprocessing.get_context("fork").Process(target=lambda: None)
proc.start()
proc.join()
assert proc.exitcode == 0
pathlib.Path("python-output.txt").write_text("python", encoding="utf-8")
PY
if command -v node >/dev/null 2>&1; then
  node -e 'require("fs").writeFileSync("node-output.txt", "node")'
fi
if command -v cc >/dev/null 2>&1; then
  printf 'int main(void) { return 0; }\n' > probe.c
  cc probe.c -o compiled-probe
  ./compiled-probe
fi
git status --short >/dev/null
git diff --no-ext-diff >/dev/null
git log -1 --oneline >/dev/null
"""
    subprocess.run(
        ["git", "init", "-q", str(workspace)],
        capture_output=True,
        text=True,
        check=True,
    )
    (workspace / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Sandbox Test",
            "-c",
            "user.email=sandbox@example.invalid",
            "add",
            "tracked.txt",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Sandbox Test",
            "-c",
            "user.email=sandbox@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    completed = subprocess.run(
        sandbox_command(["/bin/bash", "-c", command], workspace=str(workspace)),
        cwd=workspace,
        env={},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (workspace / "renamed.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (workspace / "python-output.txt").read_text(encoding="utf-8") == "python"
    if shutil.which("node"):
        assert (workspace / "node-output.txt").read_text(encoding="utf-8") == "node"


def test_tmux_session_identity_includes_network_policy(tmp_path):
    from src.agent_tools.subprocess_tools import _tmux_session_name

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    isolated = _tmux_session_name("session-1", str(workspace))
    brokered = _tmux_session_name(
        "session-1",
        str(workspace),
        network_profile=SandboxNetworkProfile.BROKERED_ONLY,
    )

    assert isolated != brokered
    assert isolated.endswith("-networkless")
    assert brokered.endswith("-brokered-only")


@requires_bubblewrap
def test_tmux_bash_shell_runs_inside_same_sandbox(
    tmp_path,
    runtime_seccomp_launcher,
):
    from src.agent_tools.subprocess_tools import (
        _run_exec,
        _run_tmux_bash,
        _tmux_session_name,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    session_id = f"sandbox-test-{uuid.uuid4().hex}"
    session_name = _tmux_session_name(session_id, str(workspace))

    async def run():
        try:
            return await _run_tmux_bash(
                f"test ! -e {outside!s} && pwd && touch tmux-write.txt",
                session_id=session_id,
                cwd=str(workspace),
                timeout=10,
            )
        finally:
            await _run_exec(
                "tmux",
                "kill-session",
                "-t",
                session_name,
                timeout=3,
            )

    stdout, stderr, returncode, timed_out = asyncio.run(run())

    assert timed_out is False
    assert returncode == 0, stderr
    assert str(workspace) in stdout
    assert (workspace / "tmux-write.txt").exists()


@requires_bubblewrap
def test_detached_background_job_uses_sandbox(
    tmp_path,
    monkeypatch,
    runtime_seccomp_launcher,
):
    from src import bg_jobs

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "jobs.json")

    record = bg_jobs.launch(
        f"test ! -e {outside!s} && printf background-ok && touch bg-write.txt",
        session_id="sandbox-session",
        cwd=str(workspace),
        max_runtime_s=10,
    )
    deadline = time.time() + 10
    current = record
    while current.get("status") == "running" and time.time() < deadline:
        time.sleep(0.05)
        current = bg_jobs.get(record["id"]) or current

    assert current["status"] == "done", current
    assert current["exit_code"] == 0
    assert "background-ok" in current["output"]
    assert (workspace / "bg-write.txt").exists()
