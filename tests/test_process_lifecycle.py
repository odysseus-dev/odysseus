"""The generic process lifecycle: identity, probes, escalation, verified death.

These pin the rules every consumer — containment, the PTY shell, the Cookbook
sweep, the browser lifecycle and kill_process_tree — inherits from one place.
"""

import asyncio
import errno
import os
import signal
import subprocess
import sys
import time

import pytest

from core import platform_compat
from src import process_lifecycle, process_ownership

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups and signals")

_IGNORE_TERM = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
)


def _spawn(code: str = _IGNORE_TERM) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    assert proc.stdout.readline().strip() == b"ready"
    return proc


def _cleanup(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    proc.stdout.close()


# ── Groups ──────────────────────────────────────────────────────────────────
@posix_only
def test_our_own_group_is_never_present_and_never_signalled(monkeypatch):
    own = os.getpgid(0)
    sent = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append(("group", pgid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(("pid", pid, sig)))

    assert process_lifecycle.group_present(own) is False
    assert process_lifecycle.signal_group(4242, own, signal.SIGTERM) is True
    assert sent == [("pid", 4242, signal.SIGTERM)]


@posix_only
def test_a_refused_group_probe_is_a_live_group(monkeypatch):
    def refuse(*_args):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", refuse)
    assert process_lifecycle.group_present(987654, own=1) is True

    def gone(*_args):
        raise ProcessLookupError(errno.ESRCH, "No such process")

    monkeypatch.setattr(os, "killpg", gone)
    assert process_lifecycle.group_present(987654, own=1) is False


@posix_only
def test_signal_group_reports_when_nothing_was_left_to_signal(monkeypatch):
    def gone(*_args):
        raise ProcessLookupError(errno.ESRCH, "No such process")

    monkeypatch.setattr(os, "killpg", gone)
    monkeypatch.setattr(os, "kill", gone)
    assert process_lifecycle.signal_group(4242, 4242, signal.SIGTERM, own=1) is False


# ── Escalation ──────────────────────────────────────────────────────────────
def _ladder():
    return ((signal.SIGTERM, 0.01), (getattr(signal, "SIGKILL", signal.SIGTERM), 0.01))


def test_escalate_does_not_signal_a_target_already_gone():
    sent = []
    result = process_lifecycle.escalate(lambda: True, sent.append, steps=_ladder())
    assert result.dead is True and result.escalated is False and sent == []


def test_escalate_terms_then_kills_and_reports_the_observed_outcome():
    sent = []
    result = process_lifecycle.escalate(lambda: False, sent.append, steps=_ladder(), poll_s=0.001)
    assert sent == [step[0] for step in _ladder()]
    assert result.dead is False and result.escalated is True


def test_escalate_stops_when_term_is_enough():
    sent = []
    result = process_lifecycle.escalate(lambda: bool(sent), sent.append, steps=_ladder())
    assert sent == [signal.SIGTERM]
    assert result.dead is True and result.escalated is False


def test_escalate_reverifies_before_kill_and_refuses_on_a_lost_identity():
    sent = []
    result = process_lifecycle.escalate(
        lambda: False, sent.append, steps=_ladder(), poll_s=0.001,
        before_step=lambda sig: "foreign",
    )
    assert sent == [signal.SIGTERM]
    assert result.refusal == "foreign" and result.dead is False


def test_escalate_stops_the_ladder_when_nothing_is_left_to_signal():
    sent = []

    def send(sig):
        sent.append(sig)
        return False

    result = process_lifecycle.escalate(lambda: False, send, steps=_ladder())
    assert sent == [signal.SIGTERM] and result.dead is False


async def test_escalate_async_signals_first_and_reaps_inside_the_window():
    sent, waited = [], []
    state = {"gone": False}

    async def wait():
        waited.append(True)
        state["gone"] = True

    result = await process_lifecycle.escalate_async(
        lambda: state["gone"], sent.append, steps=_ladder(), wait=wait,
    )
    # No precheck: an awaited leader is reaped only after the first signal.
    assert sent == [signal.SIGTERM] and waited == [True]
    assert result.dead is True and result.escalated is False


async def test_escalate_async_gives_the_reap_a_floor_even_at_zero_grace():
    seen = {}

    async def wait():
        seen["ran"] = True

    await process_lifecycle.escalate_async(
        lambda: False, lambda sig: None, steps=((signal.SIGTERM, 0.0),), wait=wait,
    )
    assert seen == {"ran": True}


# ── Identity ────────────────────────────────────────────────────────────────
def test_identity_from_record_needs_a_pid():
    assert process_lifecycle.ProcessIdentity.from_record({}) is None
    assert process_lifecycle.ProcessIdentity.from_record({"pid": "x"}) is None
    ident = process_lifecycle.ProcessIdentity.from_record(
        {"supervisor_pid": 42, "supervisor_token": "t", "pgid": "7"},
        pid_key="supervisor_pid", token_key="supervisor_token",
    )
    assert ident == process_lifecycle.ProcessIdentity(pid=42, start_token="t", pgid=7)


def test_a_pid_without_a_token_is_never_owned_and_never_exited(monkeypatch):
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: "live")
    ident = process_lifecycle.ProcessIdentity(pid=4242, start_token=None)
    assert ident.verdict() == process_ownership.UNVERIFIABLE
    assert ident.owned() is False and ident.exited() is False


@pytest.mark.parametrize("current,exited", [(None, True), ("other", True), ("mine", False)])
def test_exited_means_gone_or_recycled(monkeypatch, current, exited):
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: current)
    monkeypatch.setattr(process_lifecycle, "is_zombie", lambda pid: False)
    ident = process_lifecycle.ProcessIdentity(pid=4242, start_token="mine")
    assert ident.exited() is exited


@pytest.mark.skipif(not platform_compat.has_procfs(), reason="zombie state needs procfs")
def test_an_unreaped_zombie_has_exited():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    ident = process_lifecycle.ProcessIdentity.capture(proc.pid)
    try:
        for _ in range(250):
            if process_lifecycle.is_zombie(proc.pid):
                break
            time.sleep(0.02)
        assert ident.verdict() == process_ownership.OWNED  # still in its slot
        assert ident.exited() is True
    finally:
        proc.wait(timeout=5)


# ── Snapshot identities ─────────────────────────────────────────────────────
def test_terminate_identities_never_signals_foreign_or_unverifiable(monkeypatch):
    tokens = {1: "now-someone-else", 2: None}

    def start_token(pid):
        if pid == 3:
            raise process_ownership.InspectionUnavailable("stat")
        return tokens.get(pid)

    monkeypatch.setattr(process_ownership, "start_token", start_token)
    monkeypatch.setattr(os, "kill", lambda *a: pytest.fail("signalled an unowned pid"))
    sweep = process_lifecycle.terminate_identities(
        [process_lifecycle.ProcessIdentity(1, "mine"),
         process_lifecycle.ProcessIdentity(2, "mine"),
         process_lifecycle.ProcessIdentity(3, "mine")],
        steps=_ladder(),
    )
    assert sweep.killed == () and sweep.survivors == ()
    assert sweep.unverified == (3,) and sweep.dead is False


def test_terminate_identities_kills_in_order_and_reverifies_each_signal(monkeypatch):
    alive = {10: "a", 11: "b", 12: "c"}
    sent = []
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: alive.get(pid))
    monkeypatch.setattr(process_lifecycle, "is_zombie", lambda pid: False)

    def kill(pid, sig):
        sent.append((pid, sig))
        alive.pop(pid, None)

    monkeypatch.setattr(os, "kill", kill)
    sweep = process_lifecycle.terminate_identities(
        [process_lifecycle.ProcessIdentity(pid, token) for pid, token in list(alive.items())],
        steps=((signal.SIGTERM, 0.05),),
    )
    assert [pid for pid, _ in sent] == [10, 11, 12]
    assert sweep.killed == (10, 11, 12) and sweep.dead


@posix_only
@pytest.mark.skipif(not platform_compat.has_procfs(), reason="real identity needs procfs")
def test_terminate_identities_escalates_past_an_ignored_sigterm():
    proc = _spawn()
    try:
        ident = process_lifecycle.ProcessIdentity.capture(proc.pid)
        sweep = process_lifecycle.terminate_identities(
            [ident], steps=process_lifecycle.term_kill_steps(0.2))
        assert sweep.killed == (proc.pid,) and sweep.survivors == ()
        assert proc.wait(timeout=5) == -signal.SIGKILL
    finally:
        _cleanup(proc)


# ── terminate_tree / kill_process_tree ──────────────────────────────────────
@posix_only
@pytest.mark.skipif(not platform_compat.has_procfs(), reason="real identity needs procfs")
def test_terminate_tree_requires_a_matching_identity():
    proc = _spawn()
    try:
        refused = process_lifecycle.terminate_tree(
            proc.pid, pgid=proc.pid, start_token="procfs:not-this-process",
            require_identity=True, grace_s=0.1)
        assert refused.dead is False and refused.ownership == process_ownership.FOREIGN
        assert refused.survivors == ()
        assert proc.poll() is None

        token = process_ownership.start_token(proc.pid)
        outcome = process_lifecycle.terminate_tree(
            proc.pid, pgid=proc.pid, start_token=token, require_identity=True, grace_s=0.2)
        assert outcome.dead is True and outcome.escalated is True
    finally:
        _cleanup(proc)


@posix_only
def test_terminate_tree_keeps_a_leaderless_group_visible(monkeypatch):
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: None)
    monkeypatch.setattr(process_lifecycle, "group_present", lambda pgid, **kw: True)
    monkeypatch.setattr(process_lifecycle, "signal_group",
                        lambda *a, **kw: pytest.fail("signalled an unproven group"))
    outcome = process_lifecycle.terminate_tree(4242, pgid=4242, start_token="t",
                                               require_identity=True)
    assert outcome.dead is False and outcome.survivors == (4242,)
    assert outcome.ownership == process_ownership.GONE


@posix_only
def test_kill_process_tree_writes_no_containment_record(monkeypatch):
    from src import containment

    monkeypatch.setattr(containment, "_update_record", lambda *a, **k: pytest.fail("grant store touched"))
    proc = _spawn("import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    try:
        outcome = platform_compat.kill_process_tree(proc.pid)
        assert outcome.dead is True
        assert isinstance(outcome, containment.ReleaseOutcome)
    finally:
        _cleanup(proc)


def test_termination_outcome_shape_is_the_teardown_block():
    outcome = process_lifecycle.TerminationOutcome(dead=False, escalated=True, survivors=(1,),
                                                   mechanism="process_group", ownership="owned")
    assert outcome.to_dict() == {"dead": False, "escalated": True, "survivors": [1],
                                 "mechanism": "process_group", "ownership": "owned"}


# ── Identity-bound observation ──────────────────────────────────────────────
def _tokens(monkeypatch, sequence):
    calls = iter(sequence)

    def start_token(pid):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(process_ownership, "start_token", start_token)


def test_observe_binds_facts_to_the_token_read_around_them(monkeypatch):
    _tokens(monkeypatch, ["t1", "t1"])
    seen = process_lifecycle.observe(42, lambda pid: "facts")
    assert seen.identity == process_lifecycle.ProcessIdentity(42, "t1") and seen.facts == "facts"


def test_observe_drops_facts_read_across_a_pid_reuse(monkeypatch):
    _tokens(monkeypatch, ["old", "new"])
    assert process_lifecycle.observe(42, lambda pid: "whose facts?") is None


def test_observe_of_a_gone_pid_reads_nothing(monkeypatch):
    _tokens(monkeypatch, [None])
    assert process_lifecycle.observe(42, lambda pid: pytest.fail("read a gone pid")) is None


@pytest.mark.parametrize("sequence", [
    [process_ownership.InspectionUnavailable("stat")],
    ["t1", process_ownership.InspectionUnavailable("stat")],
])
def test_observe_keeps_unidentifiable_facts_without_a_token(monkeypatch, sequence):
    _tokens(monkeypatch, sequence)
    seen = process_lifecycle.observe(42, lambda pid: "facts")
    assert seen.identity.start_token is None and seen.facts == "facts"
    assert seen.identity.verdict() == process_ownership.UNVERIFIABLE


def test_bind_descendants_drops_a_pid_reparented_between_snapshots(monkeypatch):
    Info = process_ownership.ProcessInfo
    tables = iter([
        {10: Info(10, 1, "shell"), 11: Info(11, 10, "server")},
        {10: Info(10, 1, "shell"), 11: Info(11, 1, "stranger")},
    ])
    monkeypatch.setattr(process_ownership, "process_table", lambda: next(tables))
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: f"t{pid}")
    bound = process_lifecycle.bind_descendants([10])
    assert [seen.identity.pid for seen in bound] == [10]


def test_bind_descendants_drops_a_pid_whose_token_changed(monkeypatch):
    Info = process_ownership.ProcessInfo
    table = {10: Info(10, 1, "shell"), 11: Info(11, 10, "server")}
    monkeypatch.setattr(process_ownership, "process_table", lambda: dict(table))
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: f"t{pid}")
    monkeypatch.setattr(process_ownership, "verify",
                        lambda pid, token: process_ownership.FOREIGN if pid == 11 else process_ownership.OWNED)
    bound = process_lifecycle.bind_descendants([10], exclude={99})
    assert [(seen.identity.pid, seen.facts.command) for seen in bound] == [(10, "shell")]
