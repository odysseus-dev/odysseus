"""Process identity: is this pid still the process we started?

A recorded pid is not an identity. The kernel reuses pids, and every store in
this tree that remembers a process — ``data/bg_jobs.json``,
``data/containment_grants.json``, the Cookbook's task list — outlives the
process that wrote it, by design: those records exist so a restart does not lose
a job. The combination is the defect this module closes. A record that says
``pid 4242`` and a live ``pid 4242`` are not the same claim, and signalling the
second because the first was written is how a teardown kills a stranger.

That is not hypothetical here. ODY-86 was pid files unlinked while the daemons
they named were still live, with ownership never verified; the Cookbook survivor
sweep still terminates *any* process whose command line matches a tracked one,
which is a different spelling of the same mistake.

**The identity is (pid, start token).** A pid identifies a slot; the start token
identifies which process is occupying it. The kernel will not reissue a pid to a
process that started earlier, so comparing the token recorded at launch with the
token read now answers "is this still ours" without a handle, a lock file or a
supervisor.

Four verdicts, and the fourth is the point
------------------------------------------
:data:`OWNED`, :data:`GONE` and :data:`FOREIGN` are the answers. The fourth,
:data:`UNVERIFIABLE`, is what this host could not determine — no procfs, no
``ps``, a probe that raised, or a record written before anything recorded a
token. It is deliberately **not** collapsed into either "ours" (which would
signal strangers) or "gone" (which would abandon live processes).

Process inspection has broken off Linux four times in this tree — ODY-70, -86,
-94, -99 — every time because an inspection mechanism that was absent read as a
successful answer. So :data:`UNVERIFIABLE` is a containment failure and callers
must treat it as one: do not signal, and do not report a teardown that was not
performed. Refusing to act is the only honest option when you cannot tell what
you would be acting on.

Token granularity, stated because it bounds the guarantee
---------------------------------------------------------
======== ============================= ===============
Host     Source                        Resolution
======== ============================= ===============
Linux    boot ID + stat field 22       ~10 ms (1 tick)
macOS    ``ps -o lstart=``             1 s
Windows  ``GetProcessTimes``           100 ns
======== ============================= ===============

A pid recycled *within one token tick* is indistinguishable from the original.
On Linux and Windows that window is too small to hit in practice. On macOS it is
one second, which a pid wrap could theoretically land inside — so the token
narrows the risk by many orders of magnitude there without eliminating it. It is
a strictly better claim than the pid alone, which is the comparison that
matters; it is not a proof of identity and this module does not claim one.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Iterable, Mapping, NamedTuple, Optional

from core.platform_compat import IS_WINDOWS, PROC_ROOT, has_procfs

logger = logging.getLogger(__name__)


# ── Verdicts ────────────────────────────────────────────────────────────────
#: The pid is running and is the same process the token was taken from.
OWNED = "owned"
#: No process holds the pid. Nothing to signal and nothing to reap.
GONE = "gone"
#: A process holds the pid, and it is **not** ours — the pid was recycled.
#: Never signal a foreign pid; that is the defect, not the fix.
FOREIGN = "foreign"
#: This host could not answer. A containment failure, not a default.
UNVERIFIABLE = "unverifiable"

#: Verdicts that permit a signal. Exactly one.
SIGNALLABLE = frozenset({OWNED})


# ── Inspection mechanisms ───────────────────────────────────────────────────
MECHANISM_PROCFS = "procfs"
MECHANISM_PS = "ps"
MECHANISM_WIN32 = "win32"
#: No way to inspect processes on this host. Every verdict becomes
#: UNVERIFIABLE, which is the honest answer and not a permissive one.
MECHANISM_NONE = "none"

# The failure path only: a wedged `ps` must never hold up a teardown decision.
_PS_TIMEOUT_S = 5

#: Field 22 of ``/proc/<pid>/stat`` (1-indexed) is the process start time in
#: clock ticks since boot. Fields 1 and 2 are skipped by splitting on the last
#: ``)`` first, because a comm can itself contain spaces and parentheses.
_PROC_STAT_STARTTIME_INDEX = 19


class InspectionUnavailable(RuntimeError):
    """This host offers no way to inspect a process.

    Raised by the probes rather than returned, so a caller that forgets to
    handle it fails loudly instead of silently reading an absent mechanism as
    "the process is gone". :func:`verify` catches it and reports
    :data:`UNVERIFIABLE`.
    """

    def __init__(self, what: str) -> None:
        super().__init__(f"process inspection unavailable: cannot read {what}")
        self.what = what


def inspection_mechanism() -> str:
    """Which mechanism this host can answer identity questions with.

    Probed per call rather than cached at import: the tests substitute
    ``PROC_ROOT`` to exercise both branches on either kind of host, and a cached
    answer would pin whichever host happened to import the module first.
    """
    if IS_WINDOWS:
        return MECHANISM_WIN32
    if has_procfs():
        return MECHANISM_PROCFS
    if shutil.which("ps"):
        return MECHANISM_PS
    return MECHANISM_NONE


def inspection_available() -> bool:
    return inspection_mechanism() != MECHANISM_NONE


# ── Start tokens ────────────────────────────────────────────────────────────
def _procfs_token(pid: int) -> Optional[str]:
    try:
        raw = (PROC_ROOT / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, PermissionError) as exc:
        # The pid exists but is not readable. "I cannot tell" is not "it is
        # gone", so this must not return None.
        raise InspectionUnavailable(f"/proc/{pid}/stat ({exc})") from exc
    # comm is parenthesised and may contain spaces and ')' — split past the last.
    _, _, rest = raw.rpartition(")")
    fields = rest.split()
    try:
        ticks = fields[_PROC_STAT_STARTTIME_INDEX]
    except IndexError:
        raise InspectionUnavailable(f"/proc/{pid}/stat (unexpected layout)") from None
    try:
        boot = (PROC_ROOT / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise InspectionUnavailable(f"boot identity ({exc})") from exc
    if not boot:
        raise InspectionUnavailable("boot identity (empty)")
    # A persisted PID/start-tick pair can recur after reboot. Bind it to the
    # boot as well; older receipts cannot authorize a signal on a new boot.
    return f"procfs:{boot}:{ticks}"


def _ps_token(pid: int) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PS_TIMEOUT_S,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InspectionUnavailable(f"ps -p {pid} ({exc})") from exc
    value = (completed.stdout or "").strip()
    if completed.returncode != 0:
        # ps exits non-zero for a pid that does not exist. With no output that
        # is an absent process; with output it is a mechanism that misbehaved.
        if not value:
            return None
        raise InspectionUnavailable(f"ps -p {pid} (exit {completed.returncode})")
    if not value:
        return None
    return f"ps:{' '.join(value.split())}"


def _win32_token(pid: int) -> Optional[str]:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            raise InspectionUnavailable(f"GetProcessTimes({pid})")
        stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"win32:{stamp}"
    finally:
        kernel32.CloseHandle(handle)


def start_token(pid: Optional[int]) -> Optional[str]:
    """An opaque token identifying the process currently holding ``pid``.

    Returns None when no process holds the pid. Raises
    :class:`InspectionUnavailable` when this host cannot answer — never a
    token, and never None, for a question it could not ask.

    Record this at launch next to the pid. Compare it before signalling.
    """
    if not pid:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    mechanism = inspection_mechanism()
    if mechanism == MECHANISM_WIN32:
        return _win32_token(pid)
    if mechanism == MECHANISM_PROCFS:
        return _procfs_token(pid)
    if mechanism == MECHANISM_PS:
        return _ps_token(pid)
    raise InspectionUnavailable("process start time on this host")


def verify(pid: Optional[int], token: Optional[str]) -> str:
    """Is the process now holding ``pid`` the one ``token`` was taken from?

    Returns :data:`OWNED`, :data:`GONE`, :data:`FOREIGN` or
    :data:`UNVERIFIABLE`. Only :data:`OWNED` permits a signal.

    A missing or empty ``token`` is :data:`UNVERIFIABLE`, not :data:`OWNED`:
    a record that never captured an identity cannot establish one afterwards,
    and treating "we did not write it down" as "it is ours" is precisely the
    assumption that makes a recycled pid lethal.
    """
    if not pid:
        return GONE
    if not token:
        return UNVERIFIABLE
    try:
        current = start_token(pid)
    except InspectionUnavailable as exc:
        logger.warning("process_ownership: cannot verify pid %s: %s", pid, exc)
        return UNVERIFIABLE
    if current is None:
        return GONE
    return OWNED if current == str(token) else FOREIGN


def verify_record(
    record: Mapping[str, Any], *, pid_key: str = "pid", token_key: str = "start_token",
) -> str:
    """:func:`verify` against a stored record. Convenience for the reaper."""
    return verify((record or {}).get(pid_key), (record or {}).get(token_key))


def capture(pid: Optional[int]) -> dict[str, Any]:
    """The identity fields to persist for a process at launch.

    Always returns both keys, with ``start_token`` None when the host could not
    produce one, so a record's shape never depends on the host and a later
    reader can tell "no token" from "no field".
    """
    try:
        token = start_token(pid)
    except InspectionUnavailable as exc:
        logger.warning("process_ownership: launched pid %s without an identity: %s", pid, exc)
        token = None
    return {"pid": int(pid) if pid else None, "start_token": token}


# ── The process table ───────────────────────────────────────────────────────
class ProcessInfo(NamedTuple):
    pid: int
    ppid: int
    command: str


#: Field 4 of ``/proc/<pid>/stat`` (1-indexed) is the parent pid; it lands at
#: index 1 of the fields that follow the comm's closing paren.
_PROC_STAT_PPID_INDEX = 1


def _procfs_process_table() -> dict[int, ProcessInfo]:
    # Guarded here and not only in process_table(): a procfs scan whose
    # existence check sits in a caller is one refactor away from being an
    # unguarded scan, which is the defect tests/test_procfs_scan_guard.py pins.
    if not has_procfs():
        raise InspectionUnavailable(f"the process table via {PROC_ROOT}")
    table: dict[int, ProcessInfo] = {}
    for entry in os.listdir(PROC_ROOT):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            raw = (PROC_ROOT / entry / "cmdline").read_bytes()
            command = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except (OSError, PermissionError):
            continue
        ppid = 0
        try:
            stat = (PROC_ROOT / entry / "stat").read_text(encoding="utf-8", errors="replace")
            _, _, rest = stat.rpartition(")")
            ppid = int(rest.split()[_PROC_STAT_PPID_INDEX])
        except (OSError, PermissionError, IndexError, ValueError):
            # A kernel thread or a pid that exited mid-walk. Keeping the row
            # with ppid 0 is better than dropping it: a command-line match
            # still works, only the descendant walk loses this link.
            pass
        if command:
            table[pid] = ProcessInfo(pid=pid, ppid=ppid, command=command)
    return table


def _ps_process_table() -> dict[int, ProcessInfo]:
    try:
        completed = subprocess.run(
            # -ww defeats ps's default truncation to terminal width; without it
            # a long serve command is clipped and no match can ever be exact.
            ["ps", "-axww", "-o", "pid=,ppid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PS_TIMEOUT_S,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InspectionUnavailable(f"ps -axww ({exc})") from exc
    if completed.returncode != 0:
        raise InspectionUnavailable(f"ps -axww (exit {completed.returncode})")
    table: dict[int, ProcessInfo] = {}
    for line in (completed.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        command = parts[2].strip()
        if command:
            pid = int(parts[0])
            table[pid] = ProcessInfo(pid=pid, ppid=int(parts[1]), command=command)
    return table


def process_table() -> dict[int, ProcessInfo]:
    """Every visible process, by pid, with its parent and full command line.

    Raises :class:`InspectionUnavailable` when the host cannot enumerate
    processes, so a caller reports that it could not look rather than reporting
    that it found nothing. Those are different answers and this tree has
    conflated them before (ODY-94).

    ``ps`` covers macOS and the BSDs, which have no procfs to walk — the reason
    this exists rather than another ``/proc`` scan. procfs is preferred where
    present because it needs no subprocess.
    """
    mechanism = inspection_mechanism()
    if mechanism == MECHANISM_PROCFS:
        return _procfs_process_table()
    if mechanism == MECHANISM_PS:
        return _ps_process_table()
    # Windows: tasklist cannot report a full command line without WMI, and a
    # truncated one cannot be matched exactly. Claiming an empty table would
    # read as "no survivors".
    raise InspectionUnavailable(f"the process table via {mechanism}")


def command_lines() -> dict[int, str]:
    """Every visible pid mapped to its full command line."""
    return {pid: info.command for pid, info in process_table().items()}


def descendants(
    roots: "Iterable[int]", *, table: Optional[Mapping[int, ProcessInfo]] = None,
) -> list[int]:
    """Every process under ``roots``, roots included, breadth-first.

    The point of taking several roots and one table is that the answer is a
    *snapshot*: walking the tree one subprocess call at a time lets a child be
    reparented between calls and vanish from the result. Callers that need to
    act on a tree should capture it once, before they start tearing it down.

    A pid that is its own parent, or a cycle the table reports, terminates the
    walk rather than looping.
    """
    rows = dict(table) if table is not None else process_table()
    children: dict[int, list[int]] = {}
    for info in rows.values():
        children.setdefault(info.ppid, []).append(info.pid)

    found: list[int] = []
    seen: set[int] = set()
    queue = [int(root) for root in roots if root]
    while queue:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        found.append(pid)
        queue.extend(child for child in children.get(pid, ()) if child not in seen)
    return found
