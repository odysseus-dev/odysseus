"""Generic process lifecycle: identity, liveness, signalling, verified death.

Every runtime-owned subprocess in this tree ends the same way — something has
to decide whether a process is still the one it started, signal it without
hitting a bystander, escalate when it ignores the polite signal, and report
death only when death was observed. Before this module that sequence was
written out four times (containment's sync and async release, the PTY shell,
the Cookbook survivor sweep) and a fifth time without identity at all (the
browser tree kill), and the copies disagreed on what "dead" means and on
whether the server's own process group is fair game.

What lives here, and what deliberately does not
-----------------------------------------------
This module owns **mechanics**: process identity (pid + start token, never a
pid alone), group and pidfd probes, signal delivery, the TERM → verify → KILL
→ verify escalation, and the termination receipt. It owns no policy about
*which* processes belong to whom:

* :mod:`src.containment` decides what a grant contains — dimensions, the
  bubblewrap boundary, the namespace init, the durable grant store.
* :mod:`src.browser_lifecycle` decides which processes form a browser session
  and which files and profiles that session owns.
* Request authority and resource identity decide whether anything runs at all.
* Effects/provenance consume :class:`TerminationOutcome` as evidence; they do
  not kill.

So the engines here take the caller's ``gone()`` and ``send(sig)`` rather than
a pid: the caller knows whether "the tree" is a process group, a pidfd, a
bubblewrap namespace init or a set of snapshot identities, and this module
only guarantees the ordering, the waits, the re-verification point before
escalation, and that the outcome is the observed one.

Fail-closed rules, shared by every consumer
-------------------------------------------
* A signal requires :data:`process_ownership.OWNED`. GONE, FOREIGN and
  UNVERIFIABLE are reasons not to signal, and UNVERIFIABLE is never death.
* A refused liveness probe (``EPERM``) is a live process, never a dead one.
* The server's own process group is never probed as a child's and never
  signalled: if ``setsid`` did not apply, ``killpg`` would take the server down.
* An outcome reports ``dead=True`` only when death was observed after the last
  signal, not when a signal was sent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import select
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence

from core import platform_compat
from core.platform_compat import IS_WINDOWS, pid_alive

from src import process_ownership

logger = logging.getLogger(__name__)

OWNED = process_ownership.OWNED
GONE = process_ownership.GONE
FOREIGN = process_ownership.FOREIGN
UNVERIFIABLE = process_ownership.UNVERIFIABLE

#: How often a waiting teardown re-reads its liveness probe. Short enough that a
#: cooperative process is not waited on for the full grace, long enough not to
#: spin.
POLL_S = 0.05
#: SIGKILL cannot be caught, so a short window is enough to observe its effect.
#: Anything still present afterwards is out of reach — a zombie whose parent is
#: not us, or a process we were never entitled to signal.
KILL_WAIT_S = 1.0


# ── Receipt ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TerminationOutcome:
    """Whether the target is actually gone, not whether a signal was sent.

    Re-exported as ``containment.ReleaseOutcome``; the ``to_dict`` shape is the
    ``teardown`` block tool results and durable records already carry.
    """

    dead: bool
    escalated: bool
    survivors: tuple[int, ...] = ()
    mechanism: str = ""
    #: The ownership verdict, when teardown had to establish one. A non-empty
    #: value other than :data:`OWNED` means **no signal was sent**.
    ownership: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dead": self.dead,
            "escalated": self.escalated,
            "survivors": list(self.survivors),
            "mechanism": self.mechanism,
            "ownership": self.ownership,
        }


# ── Identity ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProcessIdentity:
    """A process as a durable claim: the pid slot *and* who occupied it.

    ``start_token`` binds the pid to one process on one boot (see
    :mod:`src.process_ownership`). An identity without a token can only ever
    verify as UNVERIFIABLE, so it can never authorise a signal.
    """

    pid: int
    start_token: Optional[str]
    pgid: Optional[int] = None

    @classmethod
    def capture(cls, pid: int, *, pgid: Optional[int] = None) -> "ProcessIdentity":
        """Identity of whatever holds ``pid`` now. Take it at launch or snapshot."""
        return cls(pid=int(pid), start_token=process_ownership.capture(pid)["start_token"],
                   pgid=pgid)

    @classmethod
    def from_record(
        cls, record: Mapping[str, Any], *, pid_key: str = "pid",
        token_key: str = "start_token", pgid_key: str = "pgid",
    ) -> Optional["ProcessIdentity"]:
        """The identity a durable record claims, or None when it names no pid."""
        record = record or {}
        try:
            pid = int(record.get(pid_key) or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            return None
        try:
            pgid = int(record.get(pgid_key) or 0) or None
        except (TypeError, ValueError):
            pgid = None
        return cls(pid=pid, start_token=record.get(token_key) or None, pgid=pgid)

    def verdict(self) -> str:
        return process_ownership.verify(self.pid, self.start_token)

    def owned(self) -> bool:
        return self.verdict() == OWNED

    def exited(self) -> bool:
        """True once the process this identity names has observably ended.

        GONE and FOREIGN both prove the original process is over — a reissued
        pid cannot coexist with the process it was taken from. A zombie has
        ended too: it runs no code and holds no resources but its exit status.
        UNVERIFIABLE is **not** an exit.
        """
        verdict = self.verdict()
        if verdict in (GONE, FOREIGN):
            return True
        return verdict == OWNED and is_zombie(self.pid)

    def to_record(self) -> dict[str, Any]:
        return {"pid": self.pid, "start_token": self.start_token, "pgid": self.pgid}


@dataclass(frozen=True)
class Observation:
    """Facts read about a process, bound to the identity they were read from."""

    identity: ProcessIdentity
    facts: Any


def observe(pid: int, read: Callable[[int], Any]) -> Optional[Observation]:
    """Read facts about ``pid`` and bind them to the process they describe.

    Membership is decided from facts — a session id, a parent, a command line —
    and a signal is authorised by identity. If the two are read separately,
    the pid can change hands in between and the identity of a stranger gets
    attached to a decision made about our process. So the start token is read
    *before* and *after* ``read(pid)``: equal tokens prove the facts belong to
    that one process, because a reissued pid always carries a later start
    time. A pid that exits or is reissued mid-read yields None — its facts
    describe no one we can name.

    When this host cannot produce a token, the facts are kept with an
    identity whose token is None: it can be reported but never signalled.
    ``read`` returning None means the pid had nothing to read (gone).
    """
    try:
        before = process_ownership.start_token(pid)
    except process_ownership.InspectionUnavailable:
        before = None
        unverifiable = True
    else:
        unverifiable = False
        if before is None:
            return None
    facts = read(pid)
    if facts is None:
        return None
    if not unverifiable:
        try:
            after = process_ownership.start_token(pid)
        except process_ownership.InspectionUnavailable:
            after, before = None, None
        else:
            if after != before:
                return None
    return Observation(identity=ProcessIdentity(pid=int(pid), start_token=before), facts=facts)


def bind_descendants(
    roots: Iterable[int], *, exclude: Iterable[int] = (),
) -> list[Observation]:
    """The processes under ``roots``, each bound to its identity.

    :func:`process_ownership.descendants` answers from one table snapshot, and
    a token captured afterwards may belong to a process that reused a pid
    after the snapshot. Here the tokens are taken between two snapshots, and a
    pid is kept only if the second snapshot still places it under ``roots``
    with the same parent and its token has not changed since. Order is the
    breadth-first order of the first snapshot; ``facts`` is the
    :class:`process_ownership.ProcessInfo` row from the confirming snapshot.
    A pid this host cannot identify is kept with a None token: reportable,
    never signallable.

    :raises process_ownership.InspectionUnavailable: no process table.
    """
    roots = [int(root) for root in roots if root]
    excluded = {int(pid) for pid in exclude}
    first = process_ownership.process_table()
    candidates = [pid for pid in process_ownership.descendants(roots, table=first)
                  if pid not in excluded]
    tokens: dict[int, Optional[str]] = {}
    for pid in candidates:
        try:
            token = process_ownership.start_token(pid)
        except process_ownership.InspectionUnavailable:
            tokens[pid] = None  # Reportable, never signallable.
            continue
        if token is not None:  # None: already gone, nothing to bind.
            tokens[pid] = token
    second = process_ownership.process_table()
    confirmed = set(process_ownership.descendants(roots, table=second))
    bound: list[Observation] = []
    for pid in candidates:
        if pid not in tokens or pid not in confirmed or pid not in second or pid not in first:
            continue
        if second[pid].ppid != first[pid].ppid:
            continue
        token = tokens[pid]
        if token is not None:
            verdict = process_ownership.verify(pid, token)
            if verdict in (GONE, FOREIGN):
                continue  # Exited or reissued since the token was taken.
            if verdict != OWNED:
                token = None  # The binding cannot be confirmed.
        bound.append(Observation(identity=ProcessIdentity(pid=pid, start_token=token),
                                 facts=second[pid]))
    return bound


def is_zombie(pid: Optional[int]) -> bool:
    """True when procfs reports ``pid`` in state ``Z``. False when it cannot tell."""
    if not pid or not platform_compat.has_procfs():
        return False
    try:
        raw = (platform_compat.PROC_ROOT / str(int(pid)) / "stat").read_text(
            encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    fields = raw.rpartition(")")[2].split()
    return bool(fields) and fields[0] == "Z"


# ── Groups ──────────────────────────────────────────────────────────────────
def own_pgid() -> int:
    try:
        return os.getpgid(0)
    except (OSError, AttributeError):  # pragma: no cover - no process groups
        return -1


def pgid_of(pid: Optional[int]) -> Optional[int]:
    """Process group of ``pid``, or None. Read it before the leader is reaped."""
    if not pid or IS_WINDOWS:
        return None
    try:
        return os.getpgid(int(pid))
    except (OSError, ProcessLookupError, ValueError, AttributeError):
        return None


def group_present(pgid: Optional[int], *, own: Optional[int] = None) -> bool:
    """True while any process remains in ``pgid``.

    ``killpg(pgid, 0)`` raising ``ProcessLookupError`` is the only proof the
    group is empty; any other refusal (EPERM) is a live group we may not
    signal. Our own group is never reported: if ``setsid`` had not applied,
    probing it would describe the server, not the child.
    """
    if not pgid or pgid <= 0 or IS_WINDOWS:
        return False
    if pgid == (own_pgid() if own is None else own):
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def signal_group(pid: Optional[int], pgid: Optional[int], sig: int, *,
                 own: Optional[int] = None) -> bool:
    """Signal the whole group, falling back to the leader alone.

    Returns whether a signal was delivered. The server's own group is never
    signalled; a pgid equal to it falls through to the single pid.
    """
    if pgid and pgid > 0 and pgid != (own_pgid() if own is None else own):
        try:
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            pass
        except OSError:
            pass  # Group signalling refused; the lone pid may still be reachable.
    if pid:
        try:
            os.kill(int(pid), sig)
            return True
        except (OSError, ValueError):
            pass
    return False


def reap_if_child(pid: Optional[int]) -> None:
    """Collect a zombie we parented, so "alive" means running.

    A zombie still answers ``kill(pid, 0)`` and still belongs to its group, so a
    process we just killed reads as a survivor until someone waits on it. Only
    synchronous teardown calls this; an awaited child is reaped by its waiter.
    """
    if not pid or IS_WINDOWS:
        return
    try:
        os.waitpid(int(pid), os.WNOHANG)
    except (ChildProcessError, OSError, ValueError):
        pass


def tree_gone(pid: Optional[int], pgid: Optional[int], *, reap: bool = False,
              own: Optional[int] = None) -> bool:
    if reap:
        reap_if_child(pid)
    return not group_present(pgid, own=own) and not pid_alive(pid)


def group_ownership_verdict(
    pid: Optional[int], pgid: Optional[int], token: Optional[str], *,
    pgid_of: Callable[[Optional[int]], Optional[int]] = pgid_of,
) -> str:
    """May a recorded (pid, pgid, token) be signalled as a group?

    The leader's identity must verify, and the recorded group must still be the
    leader's group: a valid leader does not establish ownership of an
    arbitrary recorded pgid. Anything short of that is UNVERIFIABLE.
    """
    verdict = process_ownership.verify(pid, token)
    if verdict == OWNED and not IS_WINDOWS and pgid and pgid_of(pid) != pgid:
        return UNVERIFIABLE
    return verdict


# ── pidfd ───────────────────────────────────────────────────────────────────
def pidfd_supported() -> bool:
    return hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")


def open_pidfd(pid: Optional[int]) -> Optional[int]:
    """A pidfd for ``pid``, or None when unsupported or the pid is gone.

    A pidfd names the process it was opened on, not the slot. Callers that
    open one for a recorded identity must verify the identity *after* opening:
    if it still verifies, the handle refers to that process.
    """
    if not pid or not pidfd_supported():
        return None
    try:
        return os.pidfd_open(int(pid))
    except (OSError, ValueError):
        return None


def pidfd_exited(fd: int) -> bool:
    return bool(select.select([fd], [], [], 0)[0])


def pidfd_signal(fd: int, sig: int) -> bool:
    try:
        signal.pidfd_send_signal(fd, sig)
        return True
    except OSError:
        return False


def close_fd(fd: Optional[int]) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


# ── Windows ─────────────────────────────────────────────────────────────────
def taskkill_tree(pid: int) -> None:
    """``taskkill /F /T``: Windows has no group escalation, only a forced tree kill."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        logger.warning("process_lifecycle: taskkill failed for pid %s", pid, exc_info=True)


# ── Escalation ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Escalation:
    """What an escalation observed. ``refusal`` is the caller's own re-gate result."""

    dead: bool
    escalated: bool
    refusal: Any = None


def term_kill_steps(grace_s: float, kill_wait_s: float = KILL_WAIT_S) -> tuple[tuple[int, float], ...]:
    """The standard POSIX ladder: SIGTERM, ``grace_s``, SIGKILL, ``kill_wait_s``."""
    return ((signal.SIGTERM, max(float(grace_s), 0.0)),
            (signal.SIGKILL, max(float(kill_wait_s), 0.0)))


def escalate(
    gone: Callable[[], bool],
    send: Callable[[int], Optional[bool]],
    *,
    steps: Sequence[tuple[int, float]],
    poll_s: float = POLL_S,
    precheck: bool = True,
    before_step: Optional[Callable[[int], Any]] = None,
) -> Escalation:
    """Signal through ``steps`` until ``gone()``; report the observed outcome.

    Synchronous, so it can run from shutdown, cancellation and reaper paths
    without an event loop. ``send`` returning ``False`` means nothing was left
    to signal; the ladder stops and the final probe decides. ``before_step`` is
    called before every step after the first — the point where the target's
    identity must be re-established, because the grace period is exactly long
    enough for a pid to be freed and reissued. A non-None return aborts with
    that value as :attr:`Escalation.refusal`.
    """
    escalated = False
    for index, (sig, wait_s) in enumerate(steps):
        if (precheck or index) and gone():
            return Escalation(dead=True, escalated=escalated)
        if index:
            escalated = True
            if before_step is not None:
                refusal = before_step(sig)
                if refusal is not None:
                    return Escalation(dead=False, escalated=escalated, refusal=refusal)
        if send(sig) is False:
            break
        deadline = time.monotonic() + max(wait_s, 0.0)
        while time.monotonic() < deadline and not gone():
            time.sleep(poll_s)
    return Escalation(dead=gone(), escalated=escalated)


async def escalate_async(
    gone: Callable[[], bool],
    send: Callable[[int], Optional[bool]],
    *,
    steps: Sequence[tuple[int, float]],
    wait: Optional[Callable[[], Awaitable[Any]]] = None,
    poll_s: float = POLL_S,
    precheck: bool = False,
    before_step: Optional[Callable[[int], Any]] = None,
    wait_floor_s: float = 0.05,
) -> Escalation:
    """:func:`escalate` for a child this coroutine owns.

    After each signal ``wait()`` (normally ``proc.wait``) is awaited within the
    step's window before the probe is polled: an unreaped leader is a zombie, a
    zombie is still a member of its group, and a group probe would otherwise
    report survivors for a tree that has entirely exited. ``wait_floor_s``
    gives that reap a minimum window even when the grace is zero.
    """
    escalated = False
    loop = asyncio.get_running_loop()
    for index, (sig, wait_s) in enumerate(steps):
        if (precheck or index) and gone():
            return Escalation(dead=True, escalated=escalated)
        if index:
            escalated = True
            if before_step is not None:
                refusal = before_step(sig)
                if refusal is not None:
                    return Escalation(dead=False, escalated=escalated, refusal=refusal)
        if send(sig) is False:
            break
        window = max(wait_s, wait_floor_s if wait is not None else 0.0)
        deadline = loop.time() + window
        if wait is not None:
            try:
                await asyncio.wait_for(wait(), timeout=max(deadline - loop.time(), 0.0))
            except (asyncio.TimeoutError, ProcessLookupError, ChildProcessError):
                pass
        while loop.time() < deadline and not gone():
            await asyncio.sleep(poll_s)
    return Escalation(dead=gone(), escalated=escalated)


# ── Snapshot identities ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class IdentitySweep:
    """Per-identity result of :func:`terminate_identities`.

    ``killed``: signalled by us and observed exited. ``survivors``: still the
    same live process after the last step. ``unverified``: could not be
    identified, so never signalled — reported, not silently dropped. Pids that
    had already exited before any signal appear in none of the three.
    """

    killed: tuple[int, ...] = ()
    survivors: tuple[int, ...] = ()
    unverified: tuple[int, ...] = ()

    @property
    def dead(self) -> bool:
        return not self.survivors and not self.unverified


def signal_identity(identity: ProcessIdentity, sig: int) -> bool:
    """Signal ``identity`` only while it still verifies as OWNED.

    Re-verified immediately before the signal. A pid without a start token, or
    one this host cannot inspect, is never signalled.
    """
    if identity.verdict() != OWNED:
        return False
    try:
        os.kill(identity.pid, sig)
        return True
    except (OSError, ValueError):
        return False


def terminate_identities(
    identities: Iterable[ProcessIdentity],
    *,
    steps: Sequence[tuple[int, float]],
    poll_s: float = POLL_S,
) -> IdentitySweep:
    """Escalate across a snapshot of identified processes, in the given order.

    For processes this run did not spawn and cannot hold a handle to — the
    members of a tmux pane or a browser session, enumerated from the process
    table. Every signal is preceded by a fresh verification, so a pid reissued
    during the grace period is never hit; the residual window is the gap
    between that verification and ``kill(2)`` itself.
    """
    targets = list(dict.fromkeys(identities))
    unverified = [ident for ident in targets if ident.verdict() == UNVERIFIABLE]
    pending = [ident for ident in targets if ident not in unverified and not ident.exited()]
    signalled: list[ProcessIdentity] = []
    for sig, wait_s in steps:
        pending = [ident for ident in pending if not ident.exited()]
        if not pending:
            break
        for ident in pending:
            if signal_identity(ident, sig) and ident not in signalled:
                signalled.append(ident)
        deadline = time.monotonic() + max(wait_s, 0.0)
        while time.monotonic() < deadline and any(not ident.exited() for ident in pending):
            time.sleep(poll_s)
    survivors: list[int] = []
    for ident in pending:
        if ident.exited():
            continue
        if ident.verdict() == UNVERIFIABLE:
            unverified.append(ident)
        else:
            survivors.append(ident.pid)
    return IdentitySweep(
        killed=tuple(ident.pid for ident in signalled if ident.pid not in survivors
                     and ident not in unverified),
        survivors=tuple(survivors),
        unverified=tuple(dict.fromkeys(ident.pid for ident in unverified)),
    )


# ── Compatibility teardown ──────────────────────────────────────────────────
def terminate_tree(
    pid: Optional[int],
    *,
    pgid: Optional[int] = None,
    start_token: Optional[str] = None,
    require_identity: bool = False,
    grace_s: float = 2.0,
    mechanism: Optional[str] = None,
) -> TerminationOutcome:
    """Escalating group teardown for a pid that is not a containment grant.

    With ``require_identity`` the recorded (pid, pgid, token) must pass
    :func:`group_ownership_verdict` before the first signal and again before
    SIGKILL; otherwise nothing is signalled and the verdict is reported.
    """
    name = mechanism or ("windows_tree" if IS_WINDOWS else "process_group")
    try:
        pid = int(pid) if pid else 0
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return TerminationOutcome(dead=True, escalated=False, mechanism=name)
    if not pgid:
        pgid = pgid_of(pid)

    def refusal() -> Optional[TerminationOutcome]:
        if not require_identity:
            return None
        verdict = group_ownership_verdict(pid, pgid, start_token)
        if verdict == OWNED:
            return None
        if verdict == GONE:
            # Leader death does not prove group death, and without a leader
            # nothing proves a surviving group is still ours to signal.
            alive = group_present(pgid)
            return TerminationOutcome(dead=not alive, escalated=False, mechanism=name,
                                      survivors=(pgid,) if alive and pgid else (),
                                      ownership=verdict)
        # Never list a foreign or unidentified pid as *our* survivor.
        return TerminationOutcome(dead=False, escalated=False, mechanism=name, ownership=verdict)

    refused = refusal()
    if refused is not None:
        return refused
    if IS_WINDOWS:
        taskkill_tree(pid)
        deadline = time.monotonic() + max(grace_s, 0.0)
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(POLL_S)
        return TerminationOutcome(dead=not pid_alive(pid), escalated=True, mechanism=name)
    result = escalate(
        lambda: tree_gone(pid, pgid, reap=True),
        lambda sig: signal_group(pid, pgid, sig),
        steps=term_kill_steps(grace_s),
        before_step=lambda _sig: refusal(),
    )
    if result.refusal is not None:
        return result.refusal
    survivors = () if result.dead else tuple(dict.fromkeys(v for v in (pid, pgid) if v))
    return TerminationOutcome(dead=result.dead, escalated=result.escalated,
                              survivors=survivors, mechanism=name)
