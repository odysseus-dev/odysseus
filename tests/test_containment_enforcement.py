"""Final enforcement gate: real namespaces, no fallback, and owned cancellation."""
import asyncio
import os
import subprocess
import sys
from dataclasses import replace

import pytest

from src import containment, tool_execution
from src.agent_tools import subprocess_tools


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = tmp_path / "workspace"
    path.mkdir()
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(path))
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "grants.json")
    from tests.process_resource_helpers import install_native_authority
    from src.agent_runtime import process_resources
    monkeypatch.setattr(process_resources, "_LAUNCH_DIR", tmp_path / "private" / "launches")
    install_native_authority(monkeypatch, path)
    return path


@pytest.fixture
def namespaces(workspace):
    if not containment._bwrap_available():
        pytest.skip("functional bubblewrap PID/mount namespaces unavailable")
    return workspace


def test_installed_but_nonfunctional_bwrap_is_not_available(monkeypatch):
    calls = []
    monkeypatch.setattr(containment, "IS_WINDOWS", False)
    monkeypatch.setattr(containment.shutil, "which", lambda name: "/usr/bin/bwrap")
    def blocked(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, b"", b"Operation not permitted")
    monkeypatch.setattr(containment.subprocess, "run", blocked)
    assert containment._bwrap_available() is False
    assert calls[0][0][-1] == "/bin/true"
    assert "--unshare-pid" in calls[0][0]


@pytest.mark.parametrize("tool,source", [
    (subprocess_tools.BashTool, "echo forbidden"),
    (subprocess_tools.PythonTool, "print(1 + 1)"),
])
async def test_shipped_mode_refuses_without_namespaces(tool, source, workspace, monkeypatch):
    assert containment.CONTAINMENT_MODE == containment.MODE_ENFORCING
    monkeypatch.setattr(containment, "MECHANISMS", tuple(
        item for item in containment.MECHANISMS if item.name != "bubblewrap"
    ))
    async def forbidden(*args, **kwargs):
        pytest.fail("uncontained model command spawned")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    result = await tool().execute(source, {})
    assert "containment unavailable" in result["error"]
    assert result["containment"]["executed"] is False
    assert result["containment"]["contained"] is False
    assert {"filesystem", "process_tree"} <= set(result["containment"]["unenforced_required"])


def test_process_groups_and_taskkill_do_not_claim_tree_containment(workspace):
    spec = containment.agent_spec(str(workspace), dict(os.environ), 5)
    assert containment.PROCESS_TREE not in containment._posix_group_provides(spec)
    assert containment.PROCESS_TREE not in containment._windows_provides(spec)


async def test_fully_overclaimed_group_grant_cannot_spawn(workspace, monkeypatch):
    spec = containment.agent_spec(str(workspace), {}, 5)
    forged = containment.ContainmentGrant(
        id="forged-all", mechanism="process_group", workspace=str(workspace),
        enforced=containment.DEFAULT_REQUIRED, degraded=(), unenforced_required=(),
        owner="test", mode=containment.MODE_ENFORCING, spec=spec,
    )
    async def forbidden(*args, **kwargs):
        pytest.fail("overclaimed grant reached a host spawn")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(containment.ContainmentUnavailable):
        await containment.run(forged, "echo forbidden")


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace recipe")
def test_home_interpreter_is_bound_read_only(workspace, monkeypatch):
    original = containment.shutil.which
    monkeypatch.setattr(containment.shutil, "which", lambda name:
                        "/usr/bin/bwrap" if name == "bwrap" else original(name))
    monkeypatch.setattr(sys, "prefix", "/home/test/venv")
    spec = subprocess_tools._owned_spec(str(workspace), {}, 5)
    assert "/home/test/venv" in spec.readonly_extra
    argv = containment._bwrap_prefix(spec)
    index = argv.index("/home/test/venv")
    assert argv[index - 1] == "--ro-bind"
    assert "--unshare-pid" in argv
    assert "--dev-bind" not in argv
    assert "--unshare-net" not in argv


async def test_actual_namespace_hides_host_pid_tree_and_sibling(namespaces):
    sibling = namespaces.parent / "host-secret.txt"
    sibling.write_text("original")
    host_namespace = os.readlink("/proc/self/ns/pid")
    result = await subprocess_tools.PythonTool().execute(
        "import os\n"
        "print(os.readlink('/proc/self/ns/pid'))\n"
        f"print(os.path.exists({str(sibling)!r}))\n"
        f"try:\n open({str(sibling)!r}, 'w').write('changed')\n"
        "except OSError:\n pass\n", {},
    )
    assert result["exit_code"] == 0, result
    lines = result["output"].splitlines()
    assert lines[0] != host_namespace
    assert lines[1] == "False"
    assert sibling.read_text() == "original"
    assert result["containment"]["contained"] is True
    assert result["teardown"]["dead"] is True


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux kernel handles")
async def test_dead_owner_cannot_hide_live_namespace_init(workspace, monkeypatch):
    from types import SimpleNamespace
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                                                 start_new_session=True)
    spec = containment.ContainmentSpec(str(workspace), dict(os.environ), 5, required={containment.WALL_CLOCK})
    grant = containment.acquire(spec, owner="init-life")
    token = containment.process_ownership.start_token(child.pid)
    live = replace(grant, mechanism="bubblewrap", pid=99999999, pgid=99999999)
    async def wait():
        return 0
    owner = SimpleNamespace(returncode=0, wait=wait, _ody_namespace_pid=child.pid,
                            _ody_namespace_token=token, _ody_namespace_pidfd=os.pidfd_open(child.pid))
    def denied(*args):
        raise PermissionError("EPERM")
    monkeypatch.setattr(containment.signal, "pidfd_send_signal", denied)
    try:
        result = await containment._release_awaited(live, owner, grace_s=0)
        assert result.dead is False
        assert child.pid in result.survivors
        assert child.returncode is None
        record = containment.active_grants()[0]
        assert record["release"]["dead"] is False
        assert record["released_at"] is None
    finally:
        child.kill()
        await child.wait()
        containment.release(live, grace_s=0)


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux kernel handles")
@pytest.mark.parametrize("foreign", [False, True])
async def test_recovered_namespace_init_identity_survives_owner_exit(workspace, foreign):
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                                                 start_new_session=True)
    spec = containment.ContainmentSpec(str(workspace), dict(os.environ), 5, required={containment.WALL_CLOCK})
    grant = containment.acquire(spec, owner="recovered-init")
    token = "different-boot" if foreign else containment.process_ownership.start_token(child.pid)
    containment._update_record(grant.id, pid=99999999, pgid=99999999, start_token="old-owner",
                               namespace_pid=child.pid, namespace_start_token=token, execution_started=True)
    try:
        result = containment.reap_record(containment.active_grants()[0], grace_s=0)
        assert result.dead is True
        if foreign:
            assert child.returncode is None  # Never signal a reused namespace PID.
        else:
            await asyncio.wait_for(child.wait(), 3)
            assert child.returncode is not None
        assert containment.active_grants() == []
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()


async def test_repeated_release_does_not_signal_reused_pid(workspace, monkeypatch):
    spec = containment.ContainmentSpec(str(workspace), dict(os.environ), 5, required={containment.WALL_CLOCK})
    grant = containment.acquire(spec, owner="completed")
    assert containment.release(grant).dead
    def forbidden(*args):
        pytest.fail("completed grant signalled a reused slot")
    monkeypatch.setattr(containment, "_signal_tree", forbidden)
    assert containment.release(replace(grant, pid=12345678, pgid=12345678)).dead
    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("released grant spawned another process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    with pytest.raises(ValueError, match="released grant"):
        await containment.run(grant, "echo forbidden")


async def test_default_namespace_preserves_loopback_sidecars(namespaces):
    async def reply(reader, writer):
        writer.write(b"sidecar\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(reply, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        result = await subprocess_tools.PythonTool().execute(
            f"import socket\ns=socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
            "print(s.recv(100).decode().strip())\ns.close()", {},
        )
    assert result["output"] == "sidecar", result
    assert result["containment"]["network"] == "inherit"
    assert "network" not in result["containment"]["enforced"]


async def test_namespace_handshake_closes_model_stdin(namespaces):
    result = await subprocess_tools.BashTool().execute(
        "if read value; then echo unexpected; else echo closed; fi", {},
    )
    assert result["output"] == "closed", result
    assert result["teardown"]["dead"] is True


@pytest.mark.parametrize("legacy_name", [True, False])
async def test_execution_environment_cannot_replace_probed_bwrap(namespaces, monkeypatch, legacy_name):
    bin_dir = namespaces / "bin"
    bin_dir.mkdir()
    outside = namespaces.parent / "uncontained-effect"
    impostor = bin_dir / "bwrap"
    import shlex
    impostor.write_text("#!/bin/sh\nprintf escaped > " + shlex.quote(str(outside)) + "\n")
    impostor.chmod(0o700)
    if legacy_name:
        original = containment._bwrap_prefix
        def bare_name(spec):
            argv = original(spec)
            argv[0] = "bwrap"
            return argv
        monkeypatch.setattr(containment, "_bwrap_prefix", bare_name)
    result = await subprocess_tools.BashTool().execute("printf contained", {
        "subproc_env": {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")},
    })
    if legacy_name:
        # Reproduce the old mismatch: availability probed the host binary,
        # while launch resolved a different binary through the child's PATH.
        assert "containment unavailable" in result["error"]
        assert outside.read_text() == "escaped"
    else:
        assert result["output"] == "contained", result
        assert not outside.exists()


async def test_partial_initialization_reaps_before_model_code_starts(namespaces, monkeypatch):
    from src.agent_runtime import journal
    effect = namespaces / "must-not-exist"
    def fail(*args, **kwargs):
        raise RuntimeError("initialization failed")
    monkeypatch.setattr(journal, "mark_operation_started", fail)
    result = await subprocess_tools.PythonTool().execute(
        f"open({str(effect)!r}, 'w').write('effect')", {},
    )
    assert result["exit_code"] == 1
    assert result["containment"]["executed"] is False
    assert result["containment"]["contained"] is False
    assert not effect.exists()
    assert containment.active_grants() == []


async def test_explicit_network_isolation_is_established_or_refused(namespaces):
    spec = replace(subprocess_tools._owned_spec(str(namespaces), dict(os.environ), 5),
                   network=containment.NETWORK_NONE,
                   required=containment.DEFAULT_REQUIRED | {containment.NETWORK})
    host_namespace = os.readlink("/proc/self/ns/net")
    grant = containment.acquire(spec, owner="network-hook")
    try:
        result = await containment.run(grant, [sys.executable, "-c",
            "import os; print(os.readlink('/proc/self/ns/net'))"], argv=True)
    except containment.ContainmentUnavailable:
        assert containment.active_grants() == []
    else:
        assert result.exit_code == 0
        assert result.stdout.strip() != host_namespace
        assert containment.NETWORK in result.grant.enforced
        assert result.release.dead


@pytest.mark.parametrize("exit_parent", [False, True])
async def test_setsid_daemon_cannot_survive_namespace_death(namespaces, exit_parent):
    heartbeat = namespaces / "heartbeat"
    code = (
        "import os,signal,time\n"
        "pid=os.fork()\n"
        "if pid:\n"
        + (" time.sleep(.2); os._exit(0)\n" if exit_parent else " time.sleep(60); os._exit(0)\n")
        + "os.setsid()\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        f" open({str(heartbeat)!r}, 'w').write(str(time.monotonic_ns()))\n"
        " time.sleep(.01)\n"
    )
    spec = subprocess_tools._owned_spec(str(namespaces), dict(os.environ), 1)
    result = await containment.run(containment.acquire(spec, owner="setsid"),
                                   [sys.executable, "-c", code], argv=True)
    assert heartbeat.exists(), result.stderr
    assert result.timed_out is (not exit_parent)
    assert result.release.dead is True
    last = heartbeat.read_text()
    await asyncio.sleep(.15)
    assert heartbeat.read_text() == last
    assert containment.active_grants() == []


async def test_failed_namespace_initialization_does_not_claim_containment(workspace, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(containment, "MECHANISMS", (containment.Mechanism(
        "bubblewrap", 30, lambda: True, lambda spec: containment.DEFAULT_REQUIRED,
    ),))
    async def fail(*args, **kwargs):
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_eof()
        stderr.feed_data(b"bwrap: bind failed\n")
        stderr.feed_eof()
        async def wait():
            return 1
        return SimpleNamespace(pid=99999999, stdout=stdout, stderr=stderr, returncode=1, wait=wait)
    async def release(grant, proc, **kwargs):
        outcome = containment.ReleaseOutcome(dead=True, escalated=False)
        containment._finish_release(grant, outcome)
        return outcome
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    monkeypatch.setattr(containment, "_release_awaited", release)
    result = await subprocess_tools.PythonTool().execute("print('never')", {})
    assert "containment unavailable" in result["error"]
    assert result["containment"]["executed"] is False
    assert result["containment"]["enforced"] == []
    assert containment.active_grants() == []


@pytest.mark.skipif(os.name == "nt", reason="real POSIX process")
async def test_cancellation_during_spawn_recovers_and_reaps_handle(workspace, monkeypatch):
    monkeypatch.setattr(containment, "MECHANISMS", tuple(
        item for item in containment.MECHANISMS if item.name == "process_group"
    ))
    real_spawn = asyncio.create_subprocess_exec
    spawned, return_handle = asyncio.Event(), asyncio.Event()
    children = []
    async def delayed_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        children.append(proc)
        spawned.set()
        await return_handle.wait()
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    spec = containment.ContainmentSpec(str(workspace), dict(os.environ), 10,
                                      required={containment.WALL_CLOCK})
    task = asyncio.create_task(containment.run(containment.acquire(spec, owner="spawn-cancel"),
        [sys.executable, "-c", "import time; time.sleep(60)"], argv=True))
    await asyncio.wait_for(spawned.wait(), 3)
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    return_handle.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 8)
    assert children[0].returncode is not None
    assert containment.active_grants() == []


@pytest.mark.skipif(os.name == "nt", reason="real POSIX process")
async def test_repeated_cancellation_cannot_interrupt_kill_escalation(workspace, monkeypatch):
    monkeypatch.setattr(containment, "MECHANISMS", tuple(
        item for item in containment.MECHANISMS if item.name == "process_group"
    ))
    ready = workspace / "ready"
    spec = containment.ContainmentSpec(str(workspace), dict(os.environ), 10,
                                      required={containment.WALL_CLOCK})
    task = asyncio.create_task(containment.run(containment.acquire(spec, owner="cancel"),
        [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
         f"open({str(ready)!r},'w').write('ready'); time.sleep(60)"], argv=True))
    for _ in range(100):
        if ready.exists():
            break
        await asyncio.sleep(.02)
    assert ready.exists()
    task.cancel()
    await asyncio.sleep(.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 8)
    assert containment.active_grants() == []
