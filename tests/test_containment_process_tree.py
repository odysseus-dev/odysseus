"""Teardown through the containment boundary, against real processes.

The headline case is the one that fails on an unmodified baseline: a command
that backgrounds a grandchild and then times out leaves the grandchild running,
while the tool result claims "process killed". These tests pin that the boundary
signals the whole process group and verifies death before reporting it.

POSIX only — the Windows path walks the tree with ``taskkill /T /F`` and has no
host here to run on, which is stated in the PR rather than skipped silently.
"""

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from core.platform_compat import pid_alive
from src import containment

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX process groups; Windows path untested here"
)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    store = tmp_path / "containment_grants.json"
    monkeypatch.setattr(containment, "_store_path", lambda: store)
    return store


@pytest.fixture(autouse=True)
def _real_process_group_mechanism(monkeypatch):
    """Pin the mechanism to the real POSIX process group.

    Not a fake: this is the mechanism shipped in ``MECHANISMS``, selected by
    name so the test behaves the same on a host that happens to have bubblewrap
    installed. Filesystem containment is bubblewrap's job and is not what these
    tests are about.
    """
    selected = [item for item in containment.MECHANISMS if item.name == "process_group"]
    assert selected, "process_group mechanism disappeared from MECHANISMS"
    monkeypatch.setattr(containment, "MECHANISMS", tuple(selected))
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_ENFORCING)


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return str(path)


def tree_spec(workspace, **kwargs):
    """Exercise group teardown without claiming prevention of session escape."""
    kwargs.setdefault("env", {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    kwargs.setdefault("wall_clock_s", 1)
    return containment.ContainmentSpec(
        workspace=workspace,
        required=frozenset({containment.WALL_CLOCK}),
        **kwargs,
    )


def read_pid(path, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text.isdigit():
            return int(text)
        time.sleep(0.02)
    raise AssertionError(f"{path} never received a pid")


def gone(pid, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.02)
    return not pid_alive(pid)


# ── The regression this lane exists to close ────────────────────────────────
async def test_a_timeout_leaves_no_surviving_grandchild(tmp_path, workspace):
    """A backgrounded grandchild does not survive the wall-clock kill.

    On a baseline spawn site the wrapper shell is killed with ``proc.kill()``
    and the grandchild keeps running, unowned and unreaped, while the tool
    result says the process was killed.
    """
    pidfile = tmp_path / "grandchild.pid"
    command = f"bash -c 'sleep 60 & echo $! > {pidfile}'; sleep 60"

    grant = containment.acquire(tree_spec(workspace), owner="session-1")
    result = await containment.run(grant, command)

    grandchild = read_pid(pidfile)
    assert result.timed_out is True
    assert gone(grandchild), f"grandchild {grandchild} survived the timeout kill"
    assert result.release is not None
    assert result.release.dead is True
    assert result.release.survivors == ()


async def test_the_timeout_outcome_is_observed_not_asserted(tmp_path, workspace):
    """``dead`` reflects a verified empty process group, not a signal that was sent."""
    pidfile = tmp_path / "child.pid"
    command = f"echo $$ > {pidfile}; sleep 60"
    grant = containment.acquire(tree_spec(workspace), owner="session-1")
    result = await containment.run(grant, command)
    leader = read_pid(pidfile)
    assert result.timed_out is True
    assert result.release.dead is True
    assert gone(leader)
    assert containment._group_present(result.grant.pgid) is False


async def test_a_clean_exit_tears_down_anything_left_behind(tmp_path, workspace):
    """A command that returns while leaving a background process does not leak it.

    The leftover closes its inherited pipes (``>/dev/null 2>&1``) so the command
    really does complete: a background process still holding the output pipes
    keeps the grant open until the wall clock, which is the previous test's case
    rather than this one's.
    """
    pidfile = tmp_path / "leftover.pid"
    command = f"sleep 60 >/dev/null 2>&1 & echo $! > {pidfile}; exit 0"
    grant = containment.acquire(tree_spec(workspace, wall_clock_s=10), owner="session-1")
    result = await containment.run(grant, command)
    leftover = read_pid(pidfile)
    assert result.timed_out is False
    assert result.exit_code == 0
    assert gone(leftover), f"background process {leftover} outlived its grant"
    assert result.release.dead is True


# ── Escalation ──────────────────────────────────────────────────────────────
def test_release_escalates_to_sigkill_and_reports_only_verified_death(tmp_path, workspace):
    """A SIGTERM-ignoring tree is escalated, and ``dead`` is set only once gone."""
    # The child announces itself only after installing the handler. Without that
    # the test races process startup and sometimes measures a child that was
    # still using the default SIGTERM disposition.
    ready = tmp_path / "ignoring-sigterm"
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(ready)!r}, 'w').write('x')\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists(), "child never installed its SIGTERM handler"
        grant = containment.acquire(tree_spec(workspace), owner="session-1")
        grant = replace(grant, pid=proc.pid, pgid=os.getpgid(proc.pid))
        outcome = containment.release(grant, grace_s=0.3)
        proc.wait(timeout=5)
        assert outcome.escalated is True
        assert outcome.dead is True
        assert outcome.survivors == ()
    finally:
        if proc.poll() is None:  # pragma: no cover - only on an unexpected failure
            proc.kill()
            proc.wait(timeout=5)


def test_release_does_not_escalate_a_cooperative_tree(workspace):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        grant = containment.acquire(tree_spec(workspace), owner="session-1")
        grant = replace(grant, pid=proc.pid, pgid=os.getpgid(proc.pid))
        outcome = containment.release(grant, grace_s=2.0)
        proc.wait(timeout=5)
        assert outcome.dead is True
        assert outcome.escalated is False
    finally:
        if proc.poll() is None:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=5)


def test_a_surviving_tree_keeps_its_record_active(workspace, monkeypatch):
    """A record moves to released only on verified death.

    With teardown unable to signal anything, ``release`` must report
    ``dead=False`` with the survivors named, and must not mark the grant
    released — the inverse of marking a job killed without checking.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        grant = containment.acquire(tree_spec(workspace), owner="session-1")
        grant = replace(grant, pid=proc.pid, pgid=os.getpgid(proc.pid))
        monkeypatch.setattr(containment, "_signal_tree", lambda *args, **kwargs: None)
        outcome = containment.release(grant, grace_s=0.2)
        assert outcome.dead is False
        assert outcome.escalated is True
        assert proc.pid in outcome.survivors
        active = {record["id"] for record in containment.active_grants()}
        assert grant.id in active
        assert pid_alive(proc.pid) is True
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_release_never_signals_the_servers_own_process_group(workspace, monkeypatch):
    """If setsid had not applied, killpg would take the server down with the child.

    Driven by handing teardown our own group id, which is exactly the state a
    failed setsid would leave behind.
    """
    sent = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(("pid", pid, sig)))
    containment._signal_tree(os.getpid(), os.getpgid(0), signal.SIGTERM)
    assert all(entry[0] != os.getpgid(0) for entry in sent), sent
    assert sent == [("pid", os.getpid(), signal.SIGTERM)]


def test_our_own_group_is_never_reported_as_a_childs_group():
    assert containment._group_present(os.getpgid(0)) is False


# ── run(): the contained happy path ─────────────────────────────────────────
async def test_run_returns_output_exit_code_and_a_released_record(workspace):
    grant = containment.acquire(tree_spec(workspace, wall_clock_s=10), owner="session-9")
    result = await containment.run(grant, "echo contained; exit 3")
    assert result.stdout == "contained\n"
    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.output_truncated is False
    assert result.grant.pid is not None
    assert containment.active_grants() == []


async def test_run_executes_in_the_workspace_and_with_the_declared_env_only(workspace):
    grant = containment.acquire(
        tree_spec(workspace, wall_clock_s=10, env={"PATH": "/usr/bin:/bin", "MARK": "yes"}),
        owner="session-9",
    )
    result = await containment.run(grant, 'pwd; echo "MARK=$MARK"; echo "HOME=${HOME:-unset}"')
    assert result.exit_code == 0
    assert os.path.realpath(workspace) == os.path.realpath(result.stdout.splitlines()[0])
    assert "MARK=yes" in result.stdout
    # The child env is exactly what the spec declared, never an implicit
    # inherit, so the server's own environment does not leak into it.
    assert "HOME=unset" in result.stdout


async def test_run_caps_output_and_says_so(workspace):
    grant = containment.acquire(
        tree_spec(workspace, wall_clock_s=10, max_output_bytes=16), owner="session-9",
    )
    result = await containment.run(grant, "printf 'x%.0s' $(seq 1 500); echo")
    assert result.output_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 16


async def test_run_accepts_an_argv_command_without_a_shell(workspace):
    grant = containment.acquire(tree_spec(workspace, wall_clock_s=10), owner="session-9")
    result = await containment.run(
        grant, [sys.executable, "-c", "print('argv path')"], argv=True,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "argv path"


async def test_run_feeds_stdin_when_given(workspace):
    grant = containment.acquire(tree_spec(workspace, wall_clock_s=10), owner="session-9")
    result = await containment.run(grant, "cat", stdin=b"piped\n")
    assert result.exit_code == 0
    assert result.stdout == "piped\n"


@pytest.mark.parametrize("command, argv", [("   ", False), ([], True)])
async def test_run_rejects_an_empty_command(workspace, command, argv):
    grant = containment.acquire(tree_spec(workspace, wall_clock_s=10), owner="session-9")
    with pytest.raises(ValueError, match="empty"):
        await containment.run(grant, command, argv=argv)


# ── Resource limits, where the platform provides them ───────────────────────
async def test_a_process_count_limit_is_applied_to_the_child(workspace):
    """RLIMIT_NPROC is set in the child, so the ceiling is real where it is claimed."""
    spec = containment.ContainmentSpec(
        workspace=workspace,
        env={"PATH": "/usr/bin:/bin"},
        wall_clock_s=10,
        required=frozenset({
            containment.WALL_CLOCK, containment.PROCESS_COUNT,
        }),
        max_processes=64,
    )
    grant = containment.acquire(spec, owner="session-9")
    assert containment.PROCESS_COUNT in grant.enforced
    result = await containment.run(
        grant,
        [sys.executable, "-c",
         "import resource; print(resource.getrlimit(resource.RLIMIT_NPROC))"],
        argv=True,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "(64, 64)"


async def test_a_memory_limit_is_claimed_only_where_it_can_be_applied(workspace):
    """The claim and the reality agree, on whichever platform this runs.

    macOS reports an infinite RLIMIT_AS hard limit and then refuses to lower it,
    so `memory` must come back unenforced there rather than enforced-and-crashing.
    Written to assert the consistency rather than the platform, so it is a real
    test on Linux and a real test here.
    """
    limit = 2 * 1024 * 1024 * 1024
    spec = containment.ContainmentSpec(
        workspace=workspace,
        env={"PATH": "/usr/bin:/bin"},
        wall_clock_s=10,
        required=frozenset({containment.WALL_CLOCK}),
        max_memory_bytes=limit,
    )
    grant = containment.acquire(spec, owner="session-9")
    probe = [sys.executable, "-c",
             "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"]
    result = await containment.run(grant, probe, argv=True)
    assert result.exit_code == 0, result.stderr
    if containment.MEMORY in grant.enforced:
        assert result.stdout.strip() == str(limit)
    else:
        assert containment.MEMORY in grant.degraded
        assert result.stdout.strip() != str(limit)


def test_an_unenforceable_required_limit_refuses_instead_of_crashing_the_spawn(workspace):
    """A limit this platform cannot apply is refused at acquire, not in preexec_fn.

    Skipped where the platform *can* apply it, since then there is nothing to
    refuse.
    """
    if containment._ADDRESS_SPACE_LIMIT_SUPPORTED:
        pytest.skip("this platform can lower RLIMIT_AS, so there is no shortfall")
    spec = containment.ContainmentSpec(
        workspace=workspace,
        env={"PATH": "/usr/bin:/bin"},
        wall_clock_s=10,
        required=frozenset({
            containment.WALL_CLOCK, containment.MEMORY,
        }),
        max_memory_bytes=2 * 1024 * 1024 * 1024,
    )
    with pytest.raises(containment.ContainmentUnavailable) as caught:
        containment.acquire(spec, owner="session-9")
    assert caught.value.missing == frozenset({containment.MEMORY})


def test_pid_alive_rejects_non_process_pids(monkeypatch):
    calls = []
    real_kill = os.kill

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        return real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", fake_kill)

    # Required: None, 0, and any negative integers must return False
    assert pid_alive(None) is False
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    assert pid_alive(-42) is False
    assert pid_alive(-9999) is False
    assert pid_alive("not_a_pid") is False

    # The underlying process probe (os.kill) must NEVER be invoked for pid <= 0
    assert calls == []

    # Normal positive-PID behavior remains covered
    my_pid = os.getpid()
    assert pid_alive(my_pid) is True
    assert (my_pid, 0) in calls


def test_pid_alive_retains_conservative_liveness_on_eperm(monkeypatch):
    def fake_kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", fake_kill)

    # Positive PID with EPERM is conservatively considered alive
    assert pid_alive(1) is True
    assert pid_alive(99999) is True

    # But non-process values still immediately return False without calling probe
    assert pid_alive(None) is False
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    assert pid_alive(-100) is False


def test_pid_alive_reports_false_on_process_lookup_error(monkeypatch):
    def fake_kill(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "kill", fake_kill)
    assert pid_alive(99999999) is False
