"""Process identity: the four verdicts, and that the fourth is never permissive.

Split in two. The verdict tests use **real processes**, because the claim under
test is about the kernel's behaviour — a pid that has been reaped, a pid that was
never issued, a pid whose start time differs from the one recorded — and a fake
process table cannot be wrong about that in the same ways. The mechanism tests
substitute the inspection layer, so both the procfs branch and the ``ps`` branch
are exercised on whichever kind of host happens to be running them.

The invariant worth most here is negative: :data:`process_ownership.OWNED` is the
only verdict that permits a signal, and nothing — a missing token, an absent
mechanism, a probe that raised — may produce it by default. Process inspection
has broken off Linux four times in this tree (ODY-70, -86, -94, -99), every time
because an absent mechanism read as a successful answer.
"""

import os
import signal
import subprocess

import pytest

from core import platform_compat
from src import process_ownership as po


@pytest.fixture
def sleeper():
    """A real, short-lived child in its own session. Always reaped."""
    procs = []

    def _spawn(argv=("sleep", "30")):
        proc = subprocess.Popen(list(argv), start_new_session=True)
        procs.append(proc)
        return proc

    yield _spawn
    for proc in procs:
        # Each child owns a session/process group. Keep the leader unreaped
        # until its group is signalled, so the group id cannot be recycled.
        if proc.returncode is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


# ── Verdicts, against real processes ────────────────────────────────────────
def test_a_live_process_with_its_own_token_is_owned(sleeper):
    proc = sleeper()
    token = po.start_token(proc.pid)

    assert token
    assert po.verify(proc.pid, token) == po.OWNED


def test_the_same_pid_with_a_different_token_is_foreign(sleeper):
    """The whole point: a pid is a slot, and the token says who is in it."""
    proc = sleeper()
    token = po.start_token(proc.pid)

    assert po.verify(proc.pid, str(token) + "-not-this-one") == po.FOREIGN


def test_a_reaped_process_is_gone(sleeper):
    proc = sleeper()
    token = po.start_token(proc.pid)
    proc.kill()
    proc.wait(timeout=5)

    assert po.verify(proc.pid, token) == po.GONE


def test_a_pid_that_was_never_issued_is_gone():
    # Above any plausible pid_max, so this cannot collide with a real process.
    assert po.verify(2 ** 30, "token:anything") == po.GONE


def test_a_missing_token_is_unverifiable_and_never_owned(sleeper):
    """A record that captured no identity cannot acquire one afterwards.

    This is the pre-upgrade record, and the reason it must not be OWNED is that
    treating "we did not write it down" as "it is ours" is what makes a recycled
    pid lethal.
    """
    proc = sleeper()

    assert po.verify(proc.pid, None) == po.UNVERIFIABLE
    assert po.verify(proc.pid, "") == po.UNVERIFIABLE
    assert po.UNVERIFIABLE not in po.SIGNALLABLE


def test_only_owned_permits_a_signal():
    assert po.SIGNALLABLE == frozenset({po.OWNED})


def test_a_falsy_pid_is_gone_rather_than_unverifiable():
    """Nothing to identify and nothing to signal; the record is just empty."""
    assert po.verify(None, "token:x") == po.GONE
    assert po.verify(0, "token:x") == po.GONE


def test_capture_always_returns_both_fields(sleeper):
    proc = sleeper()
    captured = po.capture(proc.pid)

    assert set(captured) == {"pid", "start_token"}
    assert captured["pid"] == proc.pid
    assert po.verify(captured["pid"], captured["start_token"]) == po.OWNED


def test_this_process_verifies_as_itself():
    assert po.verify(os.getpid(), po.start_token(os.getpid())) == po.OWNED


# ── An unavailable mechanism is a failure, not a default ────────────────────
def test_no_inspection_mechanism_yields_unverifiable(monkeypatch, sleeper):
    proc = sleeper()
    token = po.start_token(proc.pid)
    monkeypatch.setattr(po, "inspection_mechanism", lambda: po.MECHANISM_NONE)

    # Not GONE (which would abandon a live process) and not OWNED (which would
    # license a signal at an unidentified one).
    assert po.verify(proc.pid, token) == po.UNVERIFIABLE


def test_no_mechanism_makes_start_token_raise_rather_than_return_none(monkeypatch):
    """None means "no such process". A question we could not ask is not that."""
    monkeypatch.setattr(po, "inspection_mechanism", lambda: po.MECHANISM_NONE)

    with pytest.raises(po.InspectionUnavailable):
        po.start_token(os.getpid())


def test_a_probe_that_raises_is_unverifiable_not_owned(monkeypatch, sleeper):
    proc = sleeper()

    def _broken(_pid):
        raise po.InspectionUnavailable("deliberately broken probe")

    monkeypatch.setattr(po, "start_token", _broken)

    assert po.verify(proc.pid, "token:whatever") == po.UNVERIFIABLE


def test_capture_records_no_token_rather_than_failing(monkeypatch):
    """A host that cannot identify its children must still be able to launch.

    The record then reads UNVERIFIABLE forever, which is the honest outcome:
    the launch is allowed, and the later teardown refuses.
    """
    monkeypatch.setattr(po, "inspection_mechanism", lambda: po.MECHANISM_NONE)

    captured = po.capture(4242)

    assert captured == {"pid": 4242, "start_token": None}
    assert po.verify(4242, captured["start_token"]) == po.UNVERIFIABLE


def test_process_table_raises_without_any_mechanism(monkeypatch):
    monkeypatch.setattr(po, "inspection_mechanism", lambda: po.MECHANISM_NONE)

    with pytest.raises(po.InspectionUnavailable):
        po.process_table()


def test_the_procfs_table_guards_its_own_scan(monkeypatch, tmp_path):
    """Guarded in the function that scans, not only in its caller.

    tests/test_procfs_scan_guard.py pins this structurally; this pins the
    behaviour, so calling the branch directly on a procfs-less host raises
    instead of FileNotFoundError.
    """
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "absent")

    with pytest.raises(po.InspectionUnavailable):
        po._procfs_process_table()


# ── Mechanism selection ─────────────────────────────────────────────────────
def test_procfs_is_preferred_where_it_exists(monkeypatch, tmp_path):
    procfs = tmp_path / "proc"
    procfs.mkdir()
    monkeypatch.setattr(platform_compat, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "IS_WINDOWS", False)

    assert po.inspection_mechanism() == po.MECHANISM_PROCFS


def test_ps_covers_hosts_with_no_procfs(monkeypatch, tmp_path):
    """macOS and the BSDs. The reason this module is not another /proc scan."""
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "absent")
    monkeypatch.setattr(po, "IS_WINDOWS", False)
    monkeypatch.setattr(po.shutil, "which", lambda name: "/bin/ps" if name == "ps" else None)

    assert po.inspection_mechanism() == po.MECHANISM_PS
    assert po.inspection_available()


def test_a_host_with_neither_reports_none(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "absent")
    monkeypatch.setattr(po, "IS_WINDOWS", False)
    monkeypatch.setattr(po.shutil, "which", lambda _name: None)

    assert po.inspection_mechanism() == po.MECHANISM_NONE
    assert not po.inspection_available()


def test_the_procfs_token_reads_a_comm_containing_spaces_and_parens(monkeypatch, tmp_path):
    """``/proc/<pid>/stat`` field 2 is attacker-adjacent: it is the executable name.

    A process called ``my (weird) prog`` would shift every field after it if the
    parser split on whitespace, which would silently read the wrong number as the
    start time and make every verdict wrong.
    """
    procfs = tmp_path / "proc"
    (procfs / "77").mkdir(parents=True)
    # "77 (comm) S" are fields 1-3, so the filler starts numbering at 4 and
    # each value equals its own field number.
    fields = " ".join(str(index) for index in range(4, 54))
    (procfs / "77" / "stat").write_text(f"77 (my (weird) prog) S {fields}\n")
    boot_path = procfs / "sys/kernel/random/boot_id"
    boot_path.parent.mkdir(parents=True)
    boot_path.write_text("first-boot\n")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "IS_WINDOWS", False)

    assert po.start_token(77) == "procfs:first-boot:22"
    token = po.start_token(77)
    boot_path.write_text("second-boot\n")
    assert po.start_token(77) != token


# ── The process tree ────────────────────────────────────────────────────────
def test_descendants_walks_a_real_tree(sleeper):
    """The grandchild case: a shell that backgrounds work and the work itself."""
    proc = sleeper(("bash", "-c", "sleep 30 & sleep 30"))
    # Wait for the shell to have actually forked, without sleeping on a clock:
    # poll the table until the children appear or the attempts run out.
    found = []
    for _attempt in range(100):
        found = po.descendants([proc.pid])
        if len(found) >= 3:
            break

    assert proc.pid in found
    assert len(found) >= 3, f"expected the shell and its two children, got {found}"


def test_descendants_includes_the_root_even_with_no_children(sleeper):
    proc = sleeper()

    assert po.descendants([proc.pid]) == [proc.pid]


def test_descendants_takes_a_single_table_snapshot(monkeypatch):
    """One table in, one answer out — reparenting cannot hide a process.

    Walking the tree with a fresh query per level lets a child be reparented
    between queries and drop out of the result, which for a teardown means a
    process nobody signals.
    """
    rows = {
        10: po.ProcessInfo(pid=10, ppid=1, command="root"),
        11: po.ProcessInfo(pid=11, ppid=10, command="child"),
        12: po.ProcessInfo(pid=12, ppid=11, command="grandchild"),
        13: po.ProcessInfo(pid=13, ppid=1, command="unrelated"),
    }

    def _explode():
        raise AssertionError("descendants must use the table it was given")

    monkeypatch.setattr(po, "process_table", _explode)

    assert po.descendants([10], table=rows) == [10, 11, 12]


def test_descendants_terminates_on_a_parent_cycle():
    """A table can report a cycle; the walk must not spin on it."""
    rows = {
        20: po.ProcessInfo(pid=20, ppid=21, command="a"),
        21: po.ProcessInfo(pid=21, ppid=20, command="b"),
    }

    assert sorted(po.descendants([20], table=rows)) == [20, 21]


def test_the_procfs_table_reads_the_parent_pid(monkeypatch, tmp_path):
    """The procfs branch of the tree walk, exercised on a host without procfs.

    macOS runs this suite and takes the ``ps`` branch, so without a substituted
    ``/proc`` the Linux parse — which is what the deployed image uses — would be
    covered by nothing.
    """
    procfs = tmp_path / "proc"
    for pid, ppid in ((10, 1), (11, 10)):
        (procfs / str(pid)).mkdir(parents=True)
        (procfs / str(pid) / "cmdline").write_bytes(f"proc-{pid}\0--flag\0".encode())
        filler = " ".join(str(index) for index in range(5, 54))
        (procfs / str(pid) / "stat").write_text(f"{pid} (proc) S {ppid} {filler}\n")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "IS_WINDOWS", False)

    table = po.process_table()

    assert table[11].ppid == 10
    assert table[10].command == "proc-10 --flag"
    assert po.descendants([10], table=table) == [10, 11]


def test_a_procfs_row_with_an_unreadable_stat_keeps_its_command_line(
    monkeypatch, tmp_path
):
    """A kernel thread or a pid that exits mid-walk still matters to a
    command-line match; dropping the row entirely would hide it."""
    procfs = tmp_path / "proc"
    (procfs / "12").mkdir(parents=True)
    (procfs / "12" / "cmdline").write_bytes(b"orphan-cmd\0")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "PROC_ROOT", procfs)
    monkeypatch.setattr(po, "IS_WINDOWS", False)

    table = po.process_table()

    assert table[12].command == "orphan-cmd"
    assert table[12].ppid == 0


def test_command_lines_sees_this_process():
    table = po.command_lines()

    assert os.getpid() in table
    assert table[os.getpid()]
