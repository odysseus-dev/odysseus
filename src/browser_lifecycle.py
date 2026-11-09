"""Lifecycle ownership for private_browser's agent-browser sessions.

agent-browser runs a short-lived CLI client against a detached daemon. The
daemon calls ``setsid`` and every Chrome process it launches stays in that
POSIX session, so the daemon pid recorded in the session's own pid file
identifies the complete browser tree. Cleanup here is limited to that tree,
the session's runtime files and its ``agent-browser-chrome-*`` profile.

This is browser-specific ownership only: session membership, the profile
prefix, runtime files and navigation state. Process identity, verified
signalling and death observation come from :mod:`src.process_lifecycle`,
reached through the single seam ``kill_browser_tree``.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core import platform_compat
from src import process_lifecycle

PROFILE_PREFIX = "agent-browser-chrome-"
RUNTIME_SUFFIXES = (".pid", ".sock", ".stream", ".version", ".engine")

# Output that proves the browser never became ready. The daemon survives such
# a failure and a later ``close`` cannot reach a browser to shut down.
LAUNCH_FAILURE_RE = re.compile(
    r"Chrome exited early|DevToolsActivePort|No usable sandbox|"
    r"Failed to launch (?:the )?browser|Browser (?:process )?exited before",
    re.IGNORECASE,
)

OBSERVATION_ACTIONS = frozenset(
    {"snapshot", "read", "find", "evaluate", "screenshot", "scroll", "wait"}
)


def runtime_root(env: dict[str, str] | None) -> Path:
    """Directory where agent-browser keeps ``<session>.pid`` and its socket.

    Mirrors agent-browser's own resolution for the environment the daemon is
    launched with: an explicit socket directory, then the XDG runtime
    directory, then ``$HOME/.agent-browser``.
    """

    source = env or {}

    def _get(name: str) -> str:
        return str(source.get(name) or os.environ.get(name) or "").strip()

    socket_dir = _get("AGENT_BROWSER_SOCKET_DIR")
    if socket_dir:
        return Path(socket_dir)
    xdg = _get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "agent-browser"
    home = _get("HOME") or str(Path.home())
    return Path(home) / ".agent-browser"


def _read_cmdline(pid: int) -> str | None:
    try:
        return (platform_compat.PROC_ROOT / str(pid) / "cmdline").read_bytes().replace(
            b"\0", b" "
        ).decode("utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def _read_stat(pid: int) -> tuple[str, int, int] | None:
    """Return ``(state, pgid, sid)`` for a pid, or ``None`` when unreadable."""

    try:
        raw = (platform_compat.PROC_ROOT / str(pid) / "stat").read_text()
    except (OSError, UnicodeError):
        return None
    _, _, rest = raw.rpartition(")")
    fields = rest.split()
    if len(fields) < 4:
        return None
    try:
        return fields[0], int(fields[2]), int(fields[3])
    except ValueError:
        return None


def _live_pids() -> list[int]:
    if not platform_compat.has_procfs():
        return []
    pids = []
    for entry in platform_compat.PROC_ROOT.iterdir():
        if entry.name.isdigit():
            pids.append(int(entry.name))
    return pids


def daemon_pid(root: Path, key: str) -> int | None:
    """Pid recorded in this session's pid file, whether or not it is alive."""

    try:
        return int((root / f"{key}.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def is_verified_daemon(pid: int | None) -> bool:
    """Whether ``pid`` is a live agent-browser process (requires procfs)."""

    if not pid:
        return False
    stat = _read_stat(pid)
    if stat is not None and stat[0] == "Z":
        return False
    command_line = _read_cmdline(pid)
    return bool(command_line) and "agent-browser" in command_line


@dataclass(frozen=True)
class _Member:
    """One process as the membership scan saw it, bound to its identity."""

    identity: process_lifecycle.ProcessIdentity
    state: str
    pgid: int
    sid: int
    cmdline: str


def _read_member_facts(pid: int) -> tuple[tuple[str, int, int], str] | None:
    stat = _read_stat(pid)
    if stat is None:
        return None
    return stat, _read_cmdline(pid) or ""


def _snapshot() -> dict[int, _Member]:
    """Every visible process, with the facts membership is decided from.

    Each pid's stat and command line are read between two start-token reads
    (:func:`process_lifecycle.observe`), so the identity teardown later
    verifies is the identity of the very process membership was decided for —
    never one captured afterwards from a pid that may have changed hands.
    """

    snapshot: dict[int, _Member] = {}
    for pid in _live_pids():
        seen = process_lifecycle.observe(pid, _read_member_facts)
        if seen is None:
            continue
        (state, pgid, sid), cmdline = seen.facts
        snapshot[pid] = _Member(
            identity=process_lifecycle.ProcessIdentity(
                pid=pid, start_token=seen.identity.start_token, pgid=pgid),
            state=state, pgid=pgid, sid=sid, cmdline=cmdline,
        )
    return snapshot


def browser_members(leader: int) -> list[_Member]:
    """Processes owned by the browser session whose daemon pid is ``leader``.

    While the daemon is verified alive, every member of its POSIX session is
    owned. Once the daemon is gone the pid may be reused, so only Chrome
    process groups whose root carries an agent-browser profile are claimed.
    Decided from one identity-bound snapshot.
    """

    snapshot = _snapshot()
    members = [
        member for member in snapshot.values()
        if member.state != "Z" and member.sid == leader
    ]
    if not members:
        return []
    daemon = snapshot.get(leader)
    if daemon is not None and daemon.state != "Z" and "agent-browser" in daemon.cmdline:
        owned = members
    else:
        owned_groups = {member.pgid for member in members if _profiles_of([member.cmdline])}
        owned = [member for member in members if member.pgid in owned_groups]
    return sorted(owned, key=lambda member: member.identity.pid)


def browser_tree(leader: int) -> list[int]:
    """Pids owned by the browser session whose daemon pid is ``leader``."""

    return [member.identity.pid for member in browser_members(leader)]


def _profiles_of(cmdlines: list[str]) -> set[Path]:
    profiles: set[Path] = set()
    for cmdline in cmdlines:
        for token in cmdline.split():
            if not token.startswith("--user-data-dir="):
                continue
            path = Path(token.split("=", 1)[1])
            if path.name.startswith(PROFILE_PREFIX):
                profiles.add(path)
    return profiles


def kill_browser_tree(leader: int, *, settle_s: float = 1.0) -> tuple[list[int], list[int], set[Path]]:
    """SIGKILL one browser session tree and wait briefly for it to exit.

    Returns ``(killed, survivors, profile_dirs)``. Synchronous so it can run
    from cancellation and shutdown paths without awaiting.

    Which processes form the session is decided here (:func:`browser_members`);
    how they are signalled is the generic lifecycle's. Each member's identity
    is the one bound to the facts membership was decided from — never
    recaptured afterwards — and it is re-verified before the signal, so a pid
    freed and reissued at any point after the scan is never hit. A member
    whose identity cannot be established is not signalled and is reported as
    a survivor: the session still owns it, and its profile must not be
    deleted from under it.
    """

    members = browser_members(leader)
    profiles = _profiles_of([member.cmdline for member in members])
    ordered = [member for member in members if member.identity.pid != leader]
    ordered += [member for member in members if member.identity.pid == leader]
    # Browser semantics: Chrome is not asked to shut down here — the polite
    # path is the agent-browser ``close`` command. This is the forced path.
    sweep = process_lifecycle.terminate_identities(
        [member.identity for member in ordered],
        steps=((signal.SIGKILL, settle_s),), poll_s=0.02,
    )
    survivors = [member.identity.pid for member in ordered
                 if member.identity.pid in sweep.survivors or member.identity.pid in sweep.unverified]
    return list(sweep.killed), survivors, profiles


@dataclass
class CleanupReceipt:
    method: str
    daemon_pid: int | None = None
    killed: int = 0
    survivors: list[int] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    removed_profiles: int = 0
    verified: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "daemon_pid": self.daemon_pid,
            "killed": self.killed,
            "survivors": list(self.survivors),
            "removed_files": list(self.removed_files),
            "removed_profiles": self.removed_profiles,
            "verified": self.verified,
            **({"note": self.note} if self.note else {}),
        }


def force_cleanup(
    root: Path,
    key: str,
    *,
    method: str = "forced",
    pid_alive: Callable[[int], bool] = platform_compat.pid_alive,
) -> CleanupReceipt:
    """Kill this session's browser tree and remove its owned resources.

    Without procfs nothing can be attributed safely, so live processes are
    left alone and only the pid file of a dead daemon is forgotten.
    """

    pid = daemon_pid(root, key)
    receipt = CleanupReceipt(method=method, daemon_pid=pid)
    if not platform_compat.has_procfs():
        if pid and not pid_alive(pid):
            _remove_runtime_files(root, key, receipt)
            receipt.verified = True
        else:
            receipt.note = "procfs unavailable; browser ownership could not be verified"
        return receipt
    profiles: set[Path] = set()
    if pid:
        killed, survivors, profiles = kill_browser_tree(pid)
        receipt.killed = len(killed)
        receipt.survivors = survivors
    if not receipt.survivors:
        _remove_runtime_files(root, key, receipt)
        for profile in profiles:
            if profile.name.startswith(PROFILE_PREFIX) and profile.is_dir():
                shutil.rmtree(profile, ignore_errors=True)
                if not profile.exists():
                    receipt.removed_profiles += 1
    receipt.verified = not receipt.survivors and not (pid and browser_tree(pid))
    return receipt


def _remove_runtime_files(root: Path, key: str, receipt: CleanupReceipt) -> None:
    for suffix in RUNTIME_SUFFIXES:
        path = root / f"{key}{suffix}"
        try:
            path.unlink()
            receipt.removed_files.append(path.name)
        except FileNotFoundError:
            continue
        except OSError:
            continue


class StageClock:
    """Ordered stage timings for one browser call."""

    def __init__(self) -> None:
        self.stages: list[dict[str, Any]] = []
        self.extra: dict[str, Any] = {}
        self._start = time.monotonic()

    def record(self, stage: str, started: float, ok: bool, **detail: Any) -> None:
        entry = {
            "stage": stage,
            "ms": int((time.monotonic() - started) * 1000),
            "ok": bool(ok),
        }
        entry.update({k: v for k, v in detail.items() if v not in (None, "")})
        self.stages.append(entry)

    def total_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


@dataclass
class BrowserSession:
    """In-process lifecycle record for one owned agent-browser session."""

    key: str
    ephemeral: bool
    root: Path | None = None
    env: dict[str, str] | None = field(default=None, repr=False)
    command_prefix: list[str] = field(default_factory=list, repr=False)
    state: str = "idle"
    navigation_generation: int = 0
    page_url: str = ""
    failed_navigation_url: str = ""
    navigation_outcome_unknown: bool = False
    _lock: asyncio.Lock | None = field(default=None, repr=False)
    _lock_loop: Any = field(default=None, repr=False)

    def bind(self, env: dict[str, str], command_prefix: list[str]) -> None:
        """Record the environment and CLI prefix the daemon is launched with."""

        self.env = dict(env)
        self.root = runtime_root(env)
        self.command_prefix = list(command_prefix)

    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def navigated(self, url: str) -> None:
        self.navigation_generation += 1
        self.page_url = url
        self.failed_navigation_url = ""
        self.navigation_outcome_unknown = False
        self.state = "ready"

    def navigation_failed(self, url: str) -> None:
        self.failed_navigation_url = url
        self.navigation_outcome_unknown = False
        self.state = "navigation_failed"

    def navigation_unknown(self, url: str) -> None:
        """A navigation was attempted but whether it happened is unknown."""

        self.page_url = ""
        self.failed_navigation_url = url
        self.navigation_outcome_unknown = True
        self.state = "navigation_unknown"

    def discarded(self, state: str) -> None:
        """The browser and its page are gone; nothing earlier is observable."""

        self.page_url = ""
        self.failed_navigation_url = ""
        self.navigation_outcome_unknown = False
        self.state = state

    def stale_observation_note(self) -> str:
        if not self.failed_navigation_url:
            return ""
        if self.navigation_outcome_unknown:
            return (
                f"Browser lifecycle: the outcome of the most recent navigation to "
                f"{self.failed_navigation_url} is unknown. This observation may not "
                f"show {self.failed_navigation_url}."
            )
        shown = self.page_url or "an earlier page"
        return (
            f"Browser lifecycle: the most recent navigation to {self.failed_navigation_url} "
            f"failed. This observation shows {shown} (navigation "
            f"#{self.navigation_generation}), not {self.failed_navigation_url}."
        )

    def receipt(self, clock: StageClock) -> dict[str, Any]:
        payload = {
            "session": self.key,
            "ownership": "ephemeral" if self.ephemeral else "retained",
            "state": self.state,
            "navigation_generation": self.navigation_generation,
            "page_url": self.page_url,
            "stages": clock.stages,
            "elapsed_ms": clock.total_ms(),
        }
        payload.update({k: v for k, v in clock.extra.items() if v is not None})
        return payload


_SESSIONS: dict[str, BrowserSession] = {}


def session_for(key: str, ephemeral: bool) -> BrowserSession:
    record = _SESSIONS.get(key)
    if record is None:
        record = BrowserSession(key=key, ephemeral=ephemeral)
        _SESSIONS[key] = record
    return record


def forget(key: str) -> None:
    _SESSIONS.pop(key, None)


def registered(key: str) -> BrowserSession | None:
    return _SESSIONS.get(key)


def has_live_daemon(
    root: Path,
    key: str,
    *,
    pid_alive: Callable[[int], bool] = platform_compat.pid_alive,
) -> bool:
    """Whether this session has a daemon a ``close`` command could reach.

    Without procfs a live pid from our own pid file is treated as a match,
    because answering "no daemon" lets ``close`` bootstrap a fresh browser.
    """

    pid = daemon_pid(root, key)
    if not pid:
        return False
    if not platform_compat.has_procfs():
        return pid_alive(pid)
    return is_verified_daemon(pid)
