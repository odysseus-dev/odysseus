"""The runtime containment boundary.

One place decides *where* and *under what limits* an already-authorized process
may run. Nothing here decides *whether* it may run — that is request authority,
and it lives elsewhere. The chain is: request → authority decides whether →
containment decides how and where → effect inside the boundary.

Three properties this module exists to hold, in order of how badly the tree
needed them:

1. **No silent downgrade.** Today a missing sandbox binary turns into a regex
   that rewrites ``/workspace`` to the real path, with no log line and no field
   in the tool result — ``namespaced or _replace_workspace_alias(...)``. A
   string rewrite is not a containment mechanism and :func:`acquire` cannot
   return one, so that line becomes unwritable through this API.
2. **Truthful reporting.** A grant states which dimensions are actually
   enforced, which were asked for best-effort and are missing, and which were
   required and are missing. "Was that command contained?" gets one answer
   instead of none.
3. **Authoritative teardown.** :func:`release` escalates SIGTERM → SIGKILL,
   signals the whole process group, and verifies death before reporting it.
   Nothing here marks a process killed that it did not observe die.

**Containment never reads the command.** :func:`acquire` is given a spec and an
owner; the command text only reaches :func:`run`, after the boundary is fixed.
That is structural, not a convention: no model output, tool argument or chain of
reasoning can widen a boundary it is never shown to. Limits come from
:data:`DEFAULT_REQUIRED` and the caller's configuration, never from the request.

Enforcement mode
----------------
:data:`CONTAINMENT_MODE` is a module-level constant, deliberately not a setting
and not an ``ODYSSEUS_*`` variable, so that changing the posture of every
agent-reachable spawn site is a one-line reviewable diff rather than a
deployment detail.

* :data:`MODE_ENFORCING` — a required dimension that cannot be established
  raises :class:`ContainmentUnavailable` and the command does not run.
* :data:`MODE_REPORT_ONLY` — the same shortfall is recorded on the grant as
  ``unenforced_required``, logged once, and the command runs.

The shipped default is enforcing. Hosts without functional namespaces refuse
native agent execution requiring filesystem and process-tree containment.
Report-only remains an explicit internal diagnostic posture, never a tool or
deployment setting. Networking is inherited unless the spec requests isolation.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Optional

from core.atomic_io import atomic_write_json, store_transaction
from core.platform_compat import IS_WINDOWS, find_bash, pid_alive

from src import process_lifecycle, process_ownership
from src.constants import (
    CONTAINMENT_STATE_FILE,
    MAX_OUTPUT_CHARS,
    WORKSPACE_MOUNT,
)

logger = logging.getLogger(__name__)


# ── Enforcement mode ────────────────────────────────────────────────────────
MODE_ENFORCING = "enforcing"
MODE_REPORT_ONLY = "report_only"

#: Required containment must be established before model-controlled code runs.
CONTAINMENT_MODE = MODE_ENFORCING


# ── Dimensions ──────────────────────────────────────────────────────────────
FILESYSTEM = "filesystem"
PROCESS_TREE = "process_tree"
WALL_CLOCK = "wall_clock"
NETWORK = "network"
MEMORY = "memory"
PROCESS_COUNT = "process_count"

DIMENSIONS = frozenset({
    FILESYSTEM, PROCESS_TREE, WALL_CLOCK, NETWORK, MEMORY, PROCESS_COUNT,
})

#: What every model-reachable spawn must have. Filesystem scope and an
#: authoritative kill are the two the tree currently lacks; a wall clock it has
#: but does not enforce past the leader process. Network, memory and process
#: count stay best-effort until mechanisms for them exist on every platform
#: (Waves 5A/5B), because requiring a dimension no mechanism provides refuses
#: every command on every host.
DEFAULT_REQUIRED = frozenset({FILESYSTEM, PROCESS_TREE, WALL_CLOCK})

NETWORK_INHERIT = "inherit"
NETWORK_NONE = "none"

# A grant record is kept this long after release so a restart can tell a reaped
# job from one it never saw, then pruned so the store cannot grow without bound.
_RETENTION_S = 3600

# Teardown reads the group liveness probe this often while waiting out the
# grace period. Short enough that a cooperative child is not waited on for the
# full grace, long enough not to spin.
_DEATH_POLL_S = process_lifecycle.POLL_S

# Destinations a bind must never overlay: replacing the private root, the
# private /tmp or the workspace itself with a host directory would undo the
# namespace from inside the argv that builds it.
_RESERVED_BIND_DESTS = frozenset({
    "/", "/tmp", "/home", "/proc", "/dev", "/sys", WORKSPACE_MOUNT,
})

# System hierarchies where a writable overlay would invalidate the boundary
# established by bubblewrap. Reject both exact roots and all descendants.
_PROTECTED_WRITABLE_HIERARCHIES = frozenset({
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/proc",
    "/dev",
    "/sys",
    "/root",
    WORKSPACE_MOUNT,
})


def _is_protected_writable_destination(path: str) -> bool:
    normalized = os.path.abspath(path)
    if normalized in _RESERVED_BIND_DESTS:
        return True
    for root in _PROTECTED_WRITABLE_HIERARCHIES:
        if normalized == root or normalized.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False

# WORKSPACE_MOUNT is re-exported from src.constants: where the workspace is
# mounted inside a namespace is a property of the tool contract, not of this
# module, and two definitions of it would be two contracts.


class ContainmentUnavailable(RuntimeError):
    """A required dimension could not be established. Never downgraded.

    Raised by :func:`acquire` under :data:`MODE_ENFORCING`, and by :func:`run`
    whenever it is handed a grant whose postcondition does not hold — so a
    hand-built grant claiming containment it does not have cannot reach a
    spawn.
    """

    def __init__(self, missing: frozenset[str], mechanism_tried: str) -> None:
        self.missing = frozenset(missing)
        self.mechanism_tried = str(mechanism_tried or "none")
        listed = ", ".join(sorted(self.missing))
        super().__init__(
            f"containment unavailable ({listed}); strongest mechanism available "
            f"was {self.mechanism_tried!r}"
        )


# ── Records ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ContainmentSpec:
    """What the caller needs. Declarative, and contains no policy decision.

    ``required`` is the whole contract: those dimensions hold or the command
    does not run. Everything else is best-effort and is reported as fact rather
    than assumed.
    """

    workspace: str
    env: Mapping[str, str]
    wall_clock_s: int
    required: frozenset[str] = DEFAULT_REQUIRED
    network: str = NETWORK_INHERIT
    writable_extra: tuple[str, ...] = ()
    readonly_extra: tuple[str, ...] = ()
    max_output_bytes: int = MAX_OUTPUT_CHARS
    max_memory_bytes: Optional[int] = None
    max_processes: Optional[int] = None

    def __post_init__(self) -> None:
        # Freeze env into a read-only view over a private copy. The child's
        # environment is part of the boundary, so a caller holding the dict it
        # passed in must not be able to edit it after acquire() validated it.
        object.__setattr__(self, "env", MappingProxyType(dict(self.env or {})))
        object.__setattr__(self, "required", frozenset(self.required or ()))
        object.__setattr__(self, "writable_extra", tuple(self.writable_extra or ()))
        object.__setattr__(self, "readonly_extra", tuple(self.readonly_extra or ()))

    @property
    def requested(self) -> frozenset[str]:
        """Dimensions this spec actually asks about.

        A spec that leaves ``network`` inherited is not asking for network
        containment, so a mechanism without it is not degraded — it gave the
        spec everything the spec wanted.
        """
        asked = {FILESYSTEM, PROCESS_TREE, WALL_CLOCK}
        if self.network == NETWORK_NONE:
            asked.add(NETWORK)
        if self.max_memory_bytes is not None:
            asked.add(MEMORY)
        if self.max_processes is not None:
            asked.add(PROCESS_COUNT)
        return frozenset(asked)


@dataclass(frozen=True)
class ContainmentGrant:
    """What was actually established. Never a superset of the spec."""

    id: str
    mechanism: str
    workspace: str
    enforced: frozenset[str]
    degraded: tuple[str, ...]
    unenforced_required: tuple[str, ...]
    owner: str
    mode: str
    spec: ContainmentSpec
    external: bool = False
    pid: Optional[int] = None
    #: The child's process group, captured at spawn. Teardown needs it because
    #: it outlives the leader's pid: the leader can exit while the processes it
    #: backgrounded keep running in the same group.
    pgid: Optional[int] = None
    namespace_pid: Optional[int] = None
    namespace_start_token: Optional[str] = None
    endpoint: Optional[str] = None

    @property
    def contained(self) -> bool:
        """True when every required dimension is actually enforced."""
        return not self.unenforced_required

    def to_dict(self) -> dict[str, Any]:
        """The ``containment`` block a tool result carries.

        Deliberately omits ``env``: it is part of the boundary but it is also
        where credentials live, and a tool result is model-visible.
        """
        data = {
            "id": self.id,
            "mechanism": self.mechanism,
            "mode": self.mode,
            "workspace": self.workspace,
            "enforced": sorted(self.enforced),
            "degraded": list(self.degraded),
            "unenforced_required": list(self.unenforced_required),
            "contained": self.contained,
            "external": self.external,
            "requested": sorted(self.spec.requested),
            "network": self.spec.network,
        }
        if self.endpoint:
            data["endpoint"] = self.endpoint
        return data


@dataclass(frozen=True)
class ContainmentResult:
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timed_out: bool
    output_truncated: bool
    grant: ContainmentGrant
    release: Optional["ReleaseOutcome"] = None


#: The termination receipt is generic lifecycle evidence, not a containment
#: concept; the name is kept because tool results and records already carry it.
ReleaseOutcome = process_lifecycle.TerminationOutcome


# ── Mechanisms ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Mechanism:
    """A way to establish containment, and exactly what it is good for.

    ``provides`` is a function of the spec alone — never of the command — so
    mechanism selection cannot be influenced by request text.
    """

    name: str
    rank: int
    available: Callable[[], bool]
    provides: Callable[[ContainmentSpec], frozenset[str]]


def _bwrap_available() -> bool:
    if IS_WINDOWS:
        return False
    executable = shutil.which("bwrap")
    if not executable:
        return False
    try:
        # Binary installation says nothing about namespace permissions (notably
        # under Docker's normal security profile). Only trusted probe code runs.
        probe = subprocess.run(
            [executable, "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/",
             "--proc", "/proc", "--dev", "/dev", "/bin/true"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=3, check=False,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _posix_group_available() -> bool:
    return not IS_WINDOWS


def _windows_available() -> bool:
    return IS_WINDOWS


#: macOS advertises an infinite ``RLIMIT_AS`` hard limit and then refuses every
#: attempt to lower it ("current limit exceeds maximum limit"), so an
#: address-space ceiling is a Linux-only mechanism. Claiming it anywhere else
#: would produce a grant saying `memory` is enforced and a spawn that dies in
#: ``preexec_fn`` — a false claim is worse than an honest absence.
_ADDRESS_SPACE_LIMIT_SUPPORTED = sys.platform.startswith("linux")


def _rlimit_fits(name: str, requested: int) -> bool:
    """True when ``requested`` is within the inherited hard limit for ``name``.

    A soft limit above the hard limit is rejected by ``setrlimit``, so asking
    for one would abort the spawn. Checked here, where it can be reported, not
    in the child, where it can only crash.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX always has it
        return False
    which = getattr(resource, name, None)
    if which is None:
        return False
    try:
        _soft, hard = resource.getrlimit(which)
    except (OSError, ValueError):  # pragma: no cover - platform dependent
        return False
    return hard in (resource.RLIM_INFINITY, -1) or requested <= hard


def _rlimit_dimensions(spec: ContainmentSpec) -> set[str]:
    """Resource dimensions a POSIX ``setrlimit`` in the child can actually hold.

    Probed rather than assumed: a dimension is only claimed when the limit
    exists on this platform and the requested value is applicable.
    """
    if IS_WINDOWS:
        return set()
    provided: set[str] = set()
    if (
        spec.max_memory_bytes is not None
        and _ADDRESS_SPACE_LIMIT_SUPPORTED
        and _rlimit_fits("RLIMIT_AS", spec.max_memory_bytes)
    ):
        provided.add(MEMORY)
    if (
        spec.max_processes is not None
        and os.geteuid() != 0  # RLIMIT_NPROC does not limit root.
        and _rlimit_fits("RLIMIT_NPROC", spec.max_processes)
    ):
        provided.add(PROCESS_COUNT)
    return provided


def _bwrap_provides(spec: ContainmentSpec) -> frozenset[str]:
    # bwrap gives the private root and the workspace bind (filesystem), a new
    # PID namespace plus --die-with-parent (process_tree), and --unshare-net when the
    # spec asked for no network. The wall clock and the resource limits are
    # ours either way, applied to the bwrap process itself so its descendants
    # inherit them.
    provided = {FILESYSTEM, PROCESS_TREE, WALL_CLOCK} | _rlimit_dimensions(spec)
    if spec.network == NETWORK_NONE:
        provided.add(NETWORK)
    return frozenset(provided)


def _posix_group_provides(spec: ContainmentSpec) -> frozenset[str]:
    # Groups support escalating teardown, but a descendant can call setsid()
    # and escape. They cannot truthfully establish process-tree containment.
    return frozenset({WALL_CLOCK} | _rlimit_dimensions(spec))


def _windows_provides(spec: ContainmentSpec) -> frozenset[str]:
    # taskkill supports teardown, but is not a Job Object preventing escaped
    # descendants. No filesystem or process-tree containment is established.
    return frozenset({WALL_CLOCK})


#: Strongest first. Selection walks this in order and stops at the first
#: mechanism that covers ``spec.required``; if none does, the strongest
#: available one is used and the shortfall is reported (or raised, under
#: MODE_ENFORCING). Tests substitute this list to drive selection
#: deterministically without needing a real sandbox.
MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism("bubblewrap", 30, _bwrap_available, _bwrap_provides),
    Mechanism("process_group", 20, _posix_group_available, _posix_group_provides),
    Mechanism("windows_tree", 10, _windows_available, _windows_provides),
)


# ── Durable grant records ───────────────────────────────────────────────────
def _store_path() -> Path:
    return Path(CONTAINMENT_STATE_FILE)


def _load_records() -> dict[str, dict[str, Any]]:
    try:
        path = _store_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return {
                    str(key): value
                    for key, value in data.items()
                    if isinstance(value, dict)
                }
    except Exception:
        # A corrupt or unreadable store must not take out execution. The grant
        # itself is authoritative for this process; the file exists so a
        # *restart* can reap rather than orphan.
        logger.warning("containment: grant store unreadable; starting empty", exc_info=True)
    return {}


def _save_records(records: Mapping[str, dict[str, Any]]) -> bool:
    try:
        atomic_write_json(str(_store_path()), dict(records), indent=2)
        return True
    except Exception:
        logger.warning("containment: could not persist grant store", exc_info=True)
        return False


def _prune(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = time.time()
    kept = {}
    for grant_id, record in records.items():
        released = record.get("released_at")
        if released and (now - float(released)) > _RETENTION_S:
            continue
        kept[grant_id] = record
    return kept


@store_transaction(lambda: _store_path())
def _write_record(grant: ContainmentGrant) -> None:
    records = _prune(_load_records())
    records[grant.id] = {
        "id": grant.id,
        "owner": grant.owner,
        "manager_pid": os.getpid(),
        "manager_token": process_ownership.capture(os.getpid())["start_token"],
        "mechanism": grant.mechanism,
        "mode": grant.mode,
        "workspace": grant.workspace,
        "enforced": sorted(grant.enforced),
        "degraded": list(grant.degraded),
        "unenforced_required": list(grant.unenforced_required),
        "required": sorted(grant.spec.required),
        "wall_clock_s": grant.spec.wall_clock_s,
        "max_memory_bytes": grant.spec.max_memory_bytes,
        "max_processes": grant.spec.max_processes,
        "network": grant.spec.network,
        "external": grant.external,
        "pid": grant.pid,
        "pgid": grant.pgid,
        "namespace_pid": grant.namespace_pid,
        "namespace_start_token": grant.namespace_start_token,
        "endpoint": grant.endpoint,
        "acquired_at": time.time(),
        "released_at": None,
        "release": None,
    }
    _save_records(records)


@store_transaction(lambda: _store_path())
def _update_record(grant_id: str, **fields: Any) -> None:
    records = _load_records()
    record = records.get(grant_id)
    if record is None:
        return
    record.update(fields)
    records[grant_id] = record
    _save_records(records)


def active_grants() -> list[dict[str, Any]]:
    """Grant records that were never released — a restart's reaping input.

    One owner, one record, one place to ask what is running on whose behalf.
    """
    return [
        record
        for record in _prune(_load_records()).values()
        if not record.get("released_at")
    ]


@store_transaction(lambda: _store_path())
def forget(grant_id: str) -> None:
    """Drop a record outright. For a reaper that has finished with it."""
    records = _load_records()
    if records.pop(str(grant_id), None) is not None:
        _save_records(records)


# ── Spec validation ─────────────────────────────────────────────────────────
def _validate_abs_path(value: str, *, label: str) -> str:
    text = str(value or "")
    if not text or "\x00" in text:
        raise ValueError(f"containment: {label} must be a non-empty path")
    if not os.path.isabs(text):
        raise ValueError(f"containment: {label} must be absolute, got {text!r}")
    if ".." in PurePosixPath(text.replace(os.sep, "/")).parts:
        raise ValueError(f"containment: {label} must not contain '..', got {text!r}")
    return os.path.normpath(text)


def _validate_spec(spec: ContainmentSpec) -> ContainmentSpec:
    """Reject a malformed spec loudly, before any mechanism is considered.

    These are caller bugs, not platform shortfalls, so they raise ValueError in
    both modes: there is no report-only version of a workspace that is not a
    directory.
    """
    unknown = set(spec.required) - DIMENSIONS
    if unknown:
        raise ValueError(
            f"containment: unknown required dimension(s) {sorted(unknown)}; "
            f"known dimensions are {sorted(DIMENSIONS)}"
        )
    # Requiring a dimension the spec never asked for can never be satisfied,
    # so it is a contradiction rather than an unavailable mechanism.
    contradictory = set(spec.required) - set(spec.requested)
    if contradictory:
        raise ValueError(
            f"containment: required {sorted(contradictory)} but the spec does not "
            "request it (set network='none', max_memory_bytes or max_processes)"
        )
    if spec.network not in (NETWORK_INHERIT, NETWORK_NONE):
        raise ValueError(f"containment: network must be 'inherit' or 'none', got {spec.network!r}")
    if not isinstance(spec.wall_clock_s, int) or isinstance(spec.wall_clock_s, bool):
        raise ValueError("containment: wall_clock_s must be an int")
    if spec.wall_clock_s <= 0:
        raise ValueError(f"containment: wall_clock_s must be positive, got {spec.wall_clock_s}")
    if spec.max_output_bytes <= 0:
        raise ValueError("containment: max_output_bytes must be positive")
    for name, value in (("max_memory_bytes", spec.max_memory_bytes),
                        ("max_processes", spec.max_processes)):
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise ValueError(f"containment: {name} must be a positive int or None")
    for key, value in spec.env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("containment: env keys and values must be str")
        if "\x00" in key or "\x00" in value:
            raise ValueError("containment: env must not contain NUL")

    workspace = _validate_abs_path(spec.workspace, label="workspace")
    if not os.path.isdir(workspace):
        raise ValueError(f"containment: workspace is not a directory: {workspace}")
    writable = tuple(
        _validate_abs_path(path, label="writable_extra") for path in spec.writable_extra
    )
    readonly = tuple(
        _validate_abs_path(path, label="readonly_extra") for path in spec.readonly_extra
    )
    for path in writable + readonly:
        if path in _RESERVED_BIND_DESTS:
            raise ValueError(f"containment: refusing to bind over reserved path {path}")
    for path in writable:
        if _is_protected_writable_destination(path):
            raise ValueError(f"containment: refusing to bind over reserved path {path}")
    return replace(spec, workspace=workspace, writable_extra=writable, readonly_extra=readonly)


def agent_spec(
    workspace: str,
    env: Mapping[str, str],
    wall_clock_s: int,
    **overrides: Any,
) -> ContainmentSpec:
    """Build the spec for a model-reachable spawn.

    One factory so no call site can quietly pass a weaker ``required`` set:
    ``required`` is :data:`DEFAULT_REQUIRED` and is not overridable here.
    Widening or narrowing it is a change to this module, reviewed as one.
    """
    overrides.pop("required", None)
    return ContainmentSpec(
        workspace=workspace,
        env=env,
        wall_clock_s=wall_clock_s,
        required=DEFAULT_REQUIRED,
        **overrides,
    )


# ── acquire ─────────────────────────────────────────────────────────────────
def _select(spec: ContainmentSpec) -> tuple[Optional[Mechanism], frozenset[str]]:
    """Strongest-first selection. Returns the mechanism and what it provides.

    Deterministic: the only inputs are the spec and each mechanism's
    availability probe. The command is not an input and is not in scope here.
    """
    best: Optional[Mechanism] = None
    best_provided: frozenset[str] = frozenset()
    for mechanism in sorted(MECHANISMS, key=lambda item: item.rank, reverse=True):
        try:
            if not mechanism.available():
                continue
        except Exception:
            logger.warning(
                "containment: availability probe for %s failed; treating as unavailable",
                mechanism.name, exc_info=True,
            )
            continue
        provided = frozenset(mechanism.provides(spec)) & DIMENSIONS
        if best is None:
            best, best_provided = mechanism, provided
        if spec.required <= provided:
            return mechanism, provided
    return best, best_provided


@dataclass(frozen=True)
class ContainmentProbe:
    """What a spec *would* get on this host. No grant, no record, no process.

    For a spawn path that has not yet been rewritten to run through
    :func:`run` and still builds its own ``create_subprocess_*`` call. Such a
    caller still has to decide — refuse, or run and say so — and that decision
    has to come from the same mechanism table :func:`acquire` consults, or the
    tree grows a second opinion about what this host can enforce.

    Calling :func:`acquire` for the answer is the wrong shape: it writes a
    durable grant record, and a record whose pid is never filled in and whose
    :func:`release` never runs is an entry a restart reaper will keep finding.
    """

    mechanism: str
    enforced: frozenset[str]
    degraded: tuple[str, ...]
    unenforced_required: tuple[str, ...]
    mode: str

    @property
    def contained(self) -> bool:
        return not self.unenforced_required

    @property
    def refuses(self) -> bool:
        """True when this spec cannot run at all under the current mode."""
        return bool(self.unenforced_required) and self.mode == MODE_ENFORCING


def probe(spec: ContainmentSpec) -> ContainmentProbe:
    """Answer what this host can establish for ``spec``, without acquiring it.

    Same selection, same mechanism table and same arithmetic as
    :func:`acquire`; it just stops before the side effects. The command is not
    an input here either.

    :raises ValueError: the spec is malformed (a caller bug, in either mode).
    """
    spec = _validate_spec(spec)
    mechanism, provided = _select(spec)
    enforced = provided & spec.requested
    missing_required = frozenset(spec.required) - enforced
    return ContainmentProbe(
        mechanism=mechanism.name if mechanism else "none",
        enforced=enforced,
        degraded=tuple(sorted(spec.requested - enforced - spec.required)),
        unenforced_required=tuple(sorted(missing_required)),
        mode=CONTAINMENT_MODE,
    )


def acquire(spec: ContainmentSpec, *, owner: str) -> ContainmentGrant:
    """Establish containment, or refuse.

    Postcondition under :data:`MODE_ENFORCING`, asserted rather than assumed::

        spec.required <= grant.enforced

    Picks the strongest available mechanism and never substitutes a weaker one
    for a required dimension. Under :data:`MODE_REPORT_ONLY` the same shortfall
    lands in ``grant.unenforced_required`` and is logged, so the run is
    distinguishable from a contained one after the fact.

    :raises ValueError: the spec is malformed (a caller bug, in either mode).
    :raises ContainmentUnavailable: a required dimension is unavailable, under
        :data:`MODE_ENFORCING`.
    """
    owner_id = str(owner or "").strip()
    if not owner_id:
        # A process with no owner is a process nothing will reap.
        raise ValueError("containment: every grant needs an owner")
    spec = _validate_spec(spec)

    mechanism, provided = _select(spec)
    enforced = provided & spec.requested
    missing_required = frozenset(spec.required) - enforced
    name = mechanism.name if mechanism else "none"

    if missing_required and CONTAINMENT_MODE == MODE_ENFORCING:
        # The command does not run. This is the whole point: "not executed" is
        # the one outcome a model cannot mistake for success.
        raise ContainmentUnavailable(missing_required, name)

    degraded = tuple(sorted(spec.requested - enforced - spec.required))
    grant = ContainmentGrant(
        id=uuid.uuid4().hex[:12],
        mechanism=name,
        workspace=spec.workspace,
        enforced=enforced,
        degraded=degraded,
        unenforced_required=tuple(sorted(missing_required)),
        owner=owner_id,
        mode=CONTAINMENT_MODE,
        spec=spec,
    )
    if missing_required:
        logger.warning(
            "containment: grant %s for owner %s is NOT contained — required %s "
            "not enforced by mechanism %s (report-only mode)",
            grant.id, owner_id, sorted(missing_required), name,
        )
    elif degraded:
        logger.info(
            "containment: grant %s enforced %s; best-effort %s unavailable under %s",
            grant.id, sorted(enforced), list(degraded), name,
        )
    _write_record(grant)
    return grant


def declare_external_bridge(
    spec: ContainmentSpec, *, owner: str, endpoint: str,
) -> ContainmentGrant:
    """Record that execution leaves this backend entirely.

    A bridged tool runs in a process this backend does not own, so no local
    mechanism can contain it. The honest record is ``enforced=frozenset()``
    rather than a grant implying confinement; this exists so that path has a
    record at all instead of looking like an absence of one.
    """
    owner_id = str(owner or "").strip()
    if not owner_id:
        raise ValueError("containment: every grant needs an owner")
    spec = _validate_spec(spec)
    grant = ContainmentGrant(
        id=uuid.uuid4().hex[:12],
        mechanism="external_bridge",
        workspace=spec.workspace,
        enforced=frozenset(),
        degraded=(),
        unenforced_required=tuple(sorted(spec.required)),
        owner=owner_id,
        mode=CONTAINMENT_MODE,
        spec=spec,
        external=True,
        endpoint=endpoint,
    )
    logger.info(
        "containment: grant %s is external (%s); nothing local contains it",
        grant.id, endpoint,
    )
    _write_record(grant)
    return grant


def unavailable_tool_result(exc: ContainmentUnavailable, *, tool: str) -> dict[str, Any]:
    """The tool result for a request that could not be contained.

    "not executed" is stated in the error text, not inferred from a missing
    output field, so a run that could not be contained reads differently from a
    contained run that failed.
    """
    listed = ", ".join(sorted(exc.missing))
    return {
        "error": f"{tool}: containment unavailable ({listed}); command not executed",
        "exit_code": 1,
        "containment": {
            "mechanism": exc.mechanism_tried,
            "mode": CONTAINMENT_MODE,
            "enforced": [],
            "unenforced_required": sorted(exc.missing),
            "contained": False,
            "executed": False,
        },
    }


# ── Launch plumbing ─────────────────────────────────────────────────────────
def _dir_chain(path: str, mounted: tuple[str, ...] = ()) -> list[str]:
    """``--dir`` args for every ancestor of ``path`` inside the private root.

    bwrap mounts into a tmpfs root, so the destination's parents have to exist
    before the bind. Stops at the mount points the argv already creates.
    """
    args: list[str] = []
    roots = ("/usr", "/etc", *mounted)
    if any(path == root or path.startswith(root + os.sep) for root in roots):
        return args
    parents: list[str] = []
    parent = os.path.dirname(path)
    while parent not in ("/", "", "/tmp", "/etc", "/usr", WORKSPACE_MOUNT):
        parents.append(parent)
        parent = os.path.dirname(parent)
    for directory in reversed(parents):
        args.extend(("--dir", directory))
    return args


def _bwrap_prefix(spec: ContainmentSpec) -> list[str]:
    """The bubblewrap argv establishing the boundary this spec asked for.

    Note what is *not* here, versus the namespace this replaces: ``/home`` and
    ``/mnt`` are not bound read-write. Binding the user's whole home directory
    into a "workspace confinement" namespace gives back most of what the
    namespace was for. Anything a command legitimately needs outside the
    workspace is named by the spec, as ``readonly_extra`` or ``writable_extra``.
    """
    executable = shutil.which("bwrap")
    if not executable:
        raise ContainmentUnavailable(spec.required, "bubblewrap")
    args = [
        os.path.abspath(executable), "--die-with-parent", "--new-session", "--unshare-pid",
        "--tmpfs", "/",
        "--dir", "/usr", "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/sbin",
        "--dir", "/etc", "--ro-bind", "/etc", "/etc",
        "--dir", "/tmp", "--tmpfs", "/tmp",
        "--dev", "/dev", "--proc", "/proc",
    ]
    mounted: tuple[str, ...] = ()
    for flag, paths in (("--ro-bind", spec.readonly_extra), ("--bind", spec.writable_extra)):
        for path in sorted(paths, key=lambda value: (value.count(os.sep), value)):
            args.extend(_dir_chain(path, mounted))
            args.extend((flag, path, path))
            mounted += (path,)
    # Mount workspace last so a read-only ancestor never hides its writable bind.
    args.extend(("--dir", WORKSPACE_MOUNT, "--bind", spec.workspace, WORKSPACE_MOUNT))
    # Preserve absolute workspace paths in generated scripts without exposing
    # a writable parent directory.
    workspace = os.path.realpath(spec.workspace)
    if workspace not in _RESERVED_BIND_DESTS and workspace not in {"/usr", "/etc"}:
        args.extend(_dir_chain(workspace, mounted))
        args.extend(("--bind", workspace, workspace))
    if spec.network == NETWORK_NONE:
        args.append("--unshare-net")
    args.extend(("--chdir", WORKSPACE_MOUNT))
    return args


def _rlimit_preexec(grant: ContainmentGrant) -> Optional[Callable[[], None]]:
    """A child-side hook applying the limits the grant actually claimed, or None.

    Only ever applies a dimension in ``grant.enforced``, so the child cannot
    attempt a limit the probe already said this platform will refuse. If a limit
    nevertheless fails to apply, the exception aborts the spawn: an unlimited
    run under a grant that promised a ceiling is the one outcome worse than a
    loud failure.
    """
    if IS_WINDOWS:
        return None
    spec = grant.spec
    memory = spec.max_memory_bytes if MEMORY in grant.enforced else None
    processes = spec.max_processes if PROCESS_COUNT in grant.enforced else None
    if memory is None and processes is None:
        return None
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX always has it
        return None

    def _apply() -> None:  # pragma: no cover - runs in the forked child
        if memory is not None:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        if processes is not None:
            resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))

    return _apply


def _launch_argv(grant: ContainmentGrant, command: Any, *, argv: bool,
                 ready_marker: Optional[str] = None, info_fd: Optional[int] = None) -> list[str]:
    spec = grant.spec
    if argv:
        parts = [str(part) for part in command]
        if not parts:
            raise ValueError("containment: empty argv")
    else:
        text = str(command or "")
        if not text.strip():
            raise ValueError("containment: empty command")
        if grant.mechanism == "bubblewrap":
            # The namespace brings its own /bin/bash via the read-only /usr.
            parts = ["/bin/bash", "-lc", text]
        else:
            shell = find_bash()
            if not shell:
                if IS_WINDOWS:
                    raise RuntimeError("Git Bash is required for the Bash tool on Windows; install Git for Windows.")
                raise RuntimeError(
                    "containment: no POSIX shell available to run a shell command"
                )
            parts = [shell, "-c", text]
    if grant.mechanism == "bubblewrap":
        if ready_marker is not None:
            # This trusted wrapper runs only after all bwrap setup succeeds.
            # A launch-time bind/security failure must not claim containment.
            parts = ["/bin/sh", "-c",
                     'printf "%s\\n" "$1"; IFS= read -r ody_ack || exit 125; '
                     '[ "$ody_ack" = "$1" ] || exit 125; shift; exec "$@"',
                     "ody-boundary", ready_marker, *parts]
        info_args = ["--info-fd", str(info_fd)] if info_fd is not None else []
        return _bwrap_prefix(spec) + info_args + parts
    return parts


def _spawn_kwargs(grant: ContainmentGrant) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        # No setsid; the child gets its own group so a console event cannot
        # reach it, and teardown walks the tree with taskkill /T.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return kwargs
    # A separate group makes ordinary tree teardown possible. The PID
    # namespace, not setsid, prevents descendants from escaping containment.
    kwargs["start_new_session"] = True
    preexec = _rlimit_preexec(grant)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    return kwargs


async def _drain(stream, buffer: list[str], budget: list[int], output_cb=None) -> None:
    """Read a stream to EOF, keeping at most ``budget[0]`` bytes.

    Reading past the cap and discarding is deliberate: stopping the read would
    block the child on a full pipe, which turns an output cap into a hang.
    ``budget[0]`` is set to -1 once anything has actually been dropped, so the
    caller reports truncation only when bytes were lost — output that exactly
    fills the cap is not truncated.
    Each stream gets its own budget so the split between stdout and stderr does
    not depend on which reader happened to be scheduled first.
    """
    if stream is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    def emit(text):
        if not text:
            return
        buffer.append(text)
        if output_cb:
            try:
                output_cb(text)
            except OSError:
                budget[0] = -1
                logger.warning("containment: output sink failed", exc_info=True)
    while True:
        line = await stream.read(65536)
        if not line:
            emit(decoder.decode(b"", final=True))
            break
        if budget[0] < 0:
            continue
        chunk = line[:budget[0]]
        if chunk:
            emit(decoder.decode(chunk))
            if budget[0] < 0:
                continue
        budget[0] = budget[0] - len(line) if len(line) <= budget[0] else -1


async def run(
    grant: ContainmentGrant,
    command: Any,
    *,
    argv: bool = False,
    stdin: Optional[bytes] = None,
    progress_cb: Optional[Callable[[dict], Awaitable[None]]] = None,
    output_cb: Optional[Callable[[str], None]] = None,
) -> ContainmentResult:
    """Execute inside an existing grant.

    Enforces the wall clock and the output cap, and on timeout tears the tree
    down through :func:`release` so the reported outcome is the observed one.

    :raises ContainmentUnavailable: the grant's postcondition does not hold
        under :data:`MODE_ENFORCING`. Re-checked here, at the point of effect,
        so a grant that was not produced by :func:`acquire` cannot buy a spawn
        by claiming dimensions it does not have.
    """
    if grant.external:
        raise ValueError(
            "containment: an external-bridge grant describes execution this "
            "backend does not own; it cannot be run locally"
        )
    if (_load_records().get(grant.id) or {}).get("released_at"):
        raise ValueError("containment: a released grant cannot execute again")
    missing = frozenset(grant.spec.required) - frozenset(grant.enforced)
    mechanism = next((item for item in MECHANISMS if item.name == grant.mechanism), None)
    provided = mechanism.provides(grant.spec) if mechanism is not None else frozenset()
    missing |= frozenset(grant.spec.required) - provided
    overclaimed = frozenset(grant.enforced) - provided
    if (missing or overclaimed) and grant.mode == MODE_ENFORCING:
        raise ContainmentUnavailable(missing | overclaimed, grant.mechanism)

    spec = grant.spec
    marker = uuid.uuid4().hex if grant.mechanism == "bubblewrap" else None
    info_read = info_write = None
    try:
        if marker is not None:
            info_read, info_write = os.pipe()
            os.set_blocking(info_read, False)
        launch = _launch_argv(grant, command, argv=argv, ready_marker=marker, info_fd=info_write)
        spawn_kwargs = _spawn_kwargs(grant)
        if info_write is not None:
            spawn_kwargs["pass_fds"] = (info_write,)
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *launch,
            stdin=asyncio.subprocess.PIPE if stdin is not None or marker is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=spec.workspace,
            env=dict(spec.env),
            **spawn_kwargs,
        ))
        try:
            proc = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # Cancellation must not detach an OS spawn already in progress.
            # Recover its handle before propagating cancellation to the caller.
            try:
                proc = await _complete_cleanup(spawning, propagate_cancel=False)
            except Exception:
                release(grant, grace_s=0)
            else:
                if info_write is not None:
                    os.close(info_write)
                    info_write = None
                proc._ody_info_read = info_read
                live = replace(grant, pid=proc.pid, pgid=None if IS_WINDOWS else proc.pid)
                await _complete_cleanup(_release_awaited(live, proc), propagate_cancel=False)
            raise
    except BaseException:
        if "proc" not in locals():
            release(grant, grace_s=0)
        raise
    finally:
        if info_write is not None:
            os.close(info_write)
        if "proc" not in locals() and info_read is not None:
            os.close(info_read)
    proc._ody_info_read = info_read
    # start_new_session makes the child its own group leader, so the group id
    # is the child's pid. Captured here rather than at teardown: once the leader
    # exits, getpgid can no longer tell us which group its children are in.
    pgid = None if IS_WINDOWS else proc.pid
    live = replace(grant, pid=proc.pid, pgid=pgid)
    # The start token is what makes this record signallable by a *later*
    # process. Without it a restart reaper holds a pid and no way to tell
    # whether the pid is still this child or something the kernel has since
    # handed to a stranger; see src/process_ownership.py.
    try:
        _update_record(grant.id, pid=proc.pid, pgid=pgid, started_at=time.time(),
                       start_token=None if marker is not None else process_ownership.capture(proc.pid)["start_token"],
                       containment_ready=marker is None, execution_started=marker is None)
        from src.agent_runtime.journal import mark_operation_started
        mark_operation_started("subprocess", pid=proc.pid)
    except BaseException:
        await _complete_cleanup(_release_awaited(live, proc), propagate_cancel=False)
        raise

    out_buf: list[str] = []
    err_buf: list[str] = []
    out_budget = [int(spec.max_output_bytes)]
    err_budget = [int(spec.max_output_bytes)]
    started = time.time()
    readers = [asyncio.create_task(_drain(proc.stderr, err_buf, err_budget, output_cb))]
    ready = marker is None
    execution_started = marker is None
    async def _wait() -> None:
        nonlocal ready, execution_started, live
        if marker is not None:
            expected = (marker + "\n").encode("ascii")
            try:
                receipt = await proc.stdout.readexactly(len(expected))
            except (asyncio.IncompleteReadError, OSError):
                receipt = b""
            if receipt != expected:
                raise ContainmentUnavailable(spec.required, grant.mechanism)
            await _capture_namespace_identity(proc)
            live = replace(live, namespace_pid=proc._ody_namespace_pid,
                           namespace_start_token=proc._ody_namespace_token)
            # The trusted child is waiting for acknowledgment, so model code
            # cannot exit/recycle the leader before we record its identity.
            _update_record(grant.id, start_token=process_ownership.capture(proc.pid)["start_token"])
            _update_record(grant.id, namespace_pid=live.namespace_pid,
                           namespace_start_token=live.namespace_start_token)
            if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
                try:
                    proc._ody_pidfd = os.pidfd_open(proc.pid)
                except OSError:
                    raise ContainmentUnavailable(spec.required, grant.mechanism) from None
            ready = True
            _update_record(grant.id, containment_ready=True, execution_started=True)
            execution_started = True
            proc.stdin.write(expected)
            await proc.stdin.drain()
        readers.append(asyncio.create_task(_drain(proc.stdout, out_buf, out_budget, output_cb)))
        # Pipe backpressure is execution time too. Feeding a child that never
        # reads stdin must remain inside the same timeout/cancellation scope.
        if (stdin is not None or marker is not None) and proc.stdin is not None:
            try:
                if stdin is not None:
                    proc.stdin.write(stdin)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()
        await proc.wait()

    async def _progress() -> None:
        while True:
            await asyncio.sleep(2.0)
            if progress_cb:
                try:
                    tail = "\n".join(("".join(out_buf) + "".join(err_buf)).splitlines()[-12:])[-8192:]
                    await progress_cb({"elapsed_s": round(time.time() - started, 1), "tail": tail})
                except Exception:
                    pass

    progress_task = asyncio.create_task(_progress()) if progress_cb else None
    timed_out = False
    outcome: Optional[ReleaseOutcome] = None
    async def _finish() -> ReleaseOutcome:
        try:
            return await _release_awaited(live, proc)
        finally:
            if progress_task is not None:
                progress_task.cancel()
                try:
                    await progress_task
                except (asyncio.CancelledError, Exception):
                    pass
            for task in readers:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=1)
                except (asyncio.TimeoutError, Exception):
                    out_budget[0] = -1
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            _close_process_handles(proc)
    try:
        try:
            await asyncio.wait_for(_wait(), timeout=spec.wall_clock_s)
        except asyncio.TimeoutError:
            if not ready:
                raise ContainmentUnavailable(spec.required, grant.mechanism)
            timed_out = True
    except BaseException as exc:
        exc.containment_established = ready
        exc.containment_executed = execution_started
        raise
    finally:
        # Clean exit, partial initialization, timeout, and cancellation share
        # the same teardown. Repeated cancellation cannot skip escalation.
        outcome = await _complete_cleanup(_finish())
    if timed_out:
        _update_record(grant.id, timed_out=True)

    return ContainmentResult(
        stdout="".join(out_buf),
        stderr="".join(err_buf),
        exit_code=proc.returncode,
        timed_out=timed_out,
        output_truncated=out_budget[0] < 0 or err_budget[0] < 0,
        grant=live,
        release=outcome,
    )


async def _complete_cleanup(awaitable, *, propagate_cancel: bool = True):
    """Finish ownership cleanup despite further cancellation, then propagate it."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    result = task.result()
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError
    return result


async def _capture_namespace_identity(proc) -> None:
    """Read bwrap's trusted init identity before acknowledging model execution."""
    fd = getattr(proc, "_ody_info_read", None)
    if fd is None:
        return
    data = bytearray()
    try:
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                await asyncio.sleep(.01)
                continue
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 4096:
                raise ValueError("oversized namespace identity")
        info = json.loads(data)
        pid = int(info["child-pid"])
        if pid <= 0 or pid == os.getpid():
            raise ValueError("invalid namespace init identity")
        proc._ody_namespace_pid = pid
        proc._ody_namespace_token = process_ownership.capture(pid)["start_token"]
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            proc._ody_namespace_pidfd = os.pidfd_open(pid)
        elif not proc._ody_namespace_token:
            raise ValueError("namespace init identity cannot be inspected")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContainmentUnavailable(frozenset({PROCESS_TREE}), "bubblewrap") from exc
    finally:
        os.close(fd)
        proc._ody_info_read = None


# ── release ─────────────────────────────────────────────────────────────────
# Process mechanics — group probes, signalling, escalation, verified death —
# live in src.process_lifecycle, shared with the PTY shell, the Cookbook sweep,
# the browser lifecycle and core.platform_compat.kill_process_tree. What stays
# here is what a grant means: its record, its namespace init and its gate.
# The thin wrappers below are this module's seams; teardown resolves them at
# call time so a test can substitute one probe without replacing the engine.
def _own_pgid() -> int:
    return process_lifecycle.own_pgid()


def _pgid_of(pid: Optional[int]) -> Optional[int]:
    if IS_WINDOWS:
        return None
    return process_lifecycle.pgid_of(pid)


def _group_present(pgid: Optional[int]) -> bool:
    """True while any process remains in ``pgid``; never true for our own group.

    EPERM is a live group we cannot signal, not verified death.
    """
    if IS_WINDOWS:
        return False
    return process_lifecycle.group_present(pgid, own=_own_pgid())


def _signal_tree(pid: Optional[int], pgid: Optional[int], sig: int) -> None:
    """Signal the whole group, falling back to the leader; never our own group."""
    process_lifecycle.signal_group(pid, pgid, sig, own=_own_pgid())


def _reap_if_child(pid: Optional[int]) -> None:
    """Clear a zombie we parented, so "alive" means running (sync teardown only)."""
    if IS_WINDOWS:
        return
    process_lifecycle.reap_if_child(pid)


def _tree_gone(pid: Optional[int], pgid: Optional[int], *, reap: bool = False) -> bool:
    if reap:
        _reap_if_child(pid)
    return not _group_present(pgid) and not pid_alive(pid)


def _outcome_for(
    grant: ContainmentGrant, *, dead: bool, escalated: bool,
) -> ReleaseOutcome:
    survivors: tuple[int, ...] = ()
    if not dead:
        survivors = tuple(dict.fromkeys(
            value for value in (grant.pid, grant.pgid) if value
        ))
        logger.warning(
            "containment: grant %s left survivors after escalation: %s",
            grant.id, survivors,
        )
    return ReleaseOutcome(
        dead=dead,
        escalated=escalated,
        survivors=survivors,
        mechanism=grant.mechanism,
    )


def _ownership_gate(
    grant: ContainmentGrant,
    pid: int,
    pgid: Optional[int],
    token: Optional[str],
) -> Optional[ReleaseOutcome]:
    """Decide whether a recovered grant may be signalled at all.

    Returns None to let teardown proceed, or the outcome to report instead.
    Reached only for a grant recovered from the durable store — the restart and
    reaper path, where the recorded pid is a claim rather than a child this
    process is holding.

    The rule is fail-closed: **a signal requires a positive identity.** Anything
    else is reported as an undead tree rather than silently killed, because the
    alternative is sending SIGKILL to whatever the kernel has since given that
    pid to. ODY-86 was this defect; the reason the record stays active on a
    refusal is that an unreapable orphan has to remain visible instead of being
    closed out as handled.
    """
    # A valid leader identity does not establish ownership of an arbitrary
    # recorded process group: a stale or inconsistent PGID is UNVERIFIABLE.
    verdict = process_lifecycle.group_ownership_verdict(pid, pgid, token, pgid_of=_pgid_of)
    if verdict == process_ownership.OWNED:
        return None

    if verdict == process_ownership.GONE:
        # The leader is gone. Its group may still hold processes it
        # backgrounded, but with the leader unverifiable there is nothing left
        # to prove the group is still ours, and a recycled group id would mean
        # killpg hits strangers. An empty group is the clean case.
        if not _group_present(pgid):
            return replace(
                _outcome_for(grant, dead=True, escalated=False), ownership=verdict,
            )
        logger.warning(
            "containment: grant %s leader pid %s is gone but group %s still has "
            "members; not signalling a group whose ownership cannot be proven",
            grant.id, pid, pgid,
        )
        return ReleaseOutcome(
            dead=False,
            escalated=False,
            survivors=(pgid,) if pgid else (),
            mechanism=grant.mechanism,
            ownership=verdict,
        )

    if verdict == process_ownership.FOREIGN:
        logger.warning(
            "containment: grant %s records pid %s, which now belongs to a "
            "different process; refusing to signal it",
            grant.id, pid,
        )
    else:
        logger.warning(
            "containment: grant %s pid %s cannot be verified on this host (%s); "
            "refusing to signal an unidentified process",
            grant.id, pid, process_ownership.inspection_mechanism(),
        )
    return ReleaseOutcome(
        dead=False,
        escalated=False,
        # Not ours to enumerate, and listing a foreign pid as a survivor of
        # *our* grant would invite the next reaper to kill it.
        survivors=(),
        mechanism=grant.mechanism,
        ownership=verdict,
    )


def release(grant: ContainmentGrant, *, grace_s: float = 2.0,
            start_token: Optional[str] = None, require_identity: bool = False) -> ReleaseOutcome:
    """Release owner and recorded namespace init; report death only for both."""
    record = _load_records().get(grant.id, {})
    previous = record.get("release") or {}
    if record.get("released_at") and previous.get("dead"):
        # Death belongs to the completed grant, not the current occupant of a
        # reused PID slot. Repeated release must never signal it again.
        return ReleaseOutcome(dead=True, escalated=bool(previous.get("escalated")),
                              mechanism=previous.get("mechanism", grant.mechanism),
                              ownership=previous.get("ownership"))
    namespace_pid = grant.namespace_pid or record.get("namespace_pid")
    namespace_token = grant.namespace_start_token or record.get("namespace_start_token")
    owner = _release_owner(grant, grace_s=grace_s, start_token=start_token,
                           require_identity=require_identity, _record_release=False)
    outcome = owner
    if namespace_pid:
        try:
            namespace_pid = int(namespace_pid)
            if namespace_pid <= 0 or namespace_pid == os.getpid():
                raise ValueError("invalid namespace init")
        except (TypeError, ValueError):
            namespace = ReleaseOutcome(dead=False, escalated=False,
                                       ownership=process_ownership.UNVERIFIABLE)
        else:
            verdict = process_ownership.verify(namespace_pid, namespace_token) if pid_alive(namespace_pid) else process_ownership.GONE
            if verdict in (process_ownership.GONE, process_ownership.FOREIGN):
                # Reusing PID 1's host slot proves its original namespace has
                # completed death; never signal its new occupant.
                namespace = ReleaseOutcome(dead=True, escalated=False, ownership=verdict)
            else:
                target = replace(grant, id=grant.id + ":namespace", mechanism="process_group",
                                 pid=namespace_pid, pgid=None, namespace_pid=None,
                                 namespace_start_token=None)
                # Opened before the identity gate inside _release_owner runs:
                # a pidfd that still verifies afterwards names that process.
                namespace_fd = process_lifecycle.open_pidfd(namespace_pid)
                try:
                    namespace = _release_owner(target, grace_s=grace_s, start_token=namespace_token,
                                               require_identity=True, _record_release=False,
                                               _pidfd=namespace_fd)
                finally:
                    process_lifecycle.close_fd(namespace_fd)
        outcome = replace(owner, dead=owner.dead and namespace.dead,
                          escalated=owner.escalated or namespace.escalated,
                          survivors=tuple(dict.fromkeys((*owner.survivors, *namespace.survivors))))
    elif grant.mechanism == "bubblewrap" and record.get("execution_started"):
        # A pre-upgrade receipt lacks proof of namespace completion. Keep it
        # visible rather than declaring a potentially blocked tree dead.
        outcome = replace(owner, dead=False, ownership=process_ownership.UNVERIFIABLE)
    _finish_release(grant, outcome)
    return outcome


def _release_owner(grant: ContainmentGrant, *, grace_s: float = 2.0,
                   start_token: Optional[str] = None, require_identity: bool = False,
                   _record_release: bool = True, _pidfd: Optional[int] = None) -> ReleaseOutcome:
    """Authoritative teardown: signal the group, escalate, then verify.

    Returns whether the tree is **observed** gone. A caller must not record a
    process as killed on anything weaker than ``dead=True`` — reporting an
    outcome you did not achieve is how a surviving process becomes invisible.

    This is the synchronous form, for a grant whose process this caller is not
    awaiting: a restart reaper, or a detached job. For a child being awaited,
    :func:`run` uses the async form, which reaps the leader before verifying —
    a zombie still belongs to its process group, so the group probe would
    otherwise report a tree that is already gone.
    """
    def finish(target, outcome):
        if _record_release:
            _finish_release(target, outcome)

    pid, pgid = grant.pid, grant.pgid
    # A grant that carries its own pid belongs to the process holding it: this
    # caller launched the child and no identity question arises. A grant whose
    # pid had to be recovered from the durable store is the restart case, and
    # there the pid is a *claim* about a process this run never started.
    recovered = pid is None
    token: Optional[str] = start_token
    if pid is None or (pgid is None and not IS_WINDOWS):
        record = _load_records().get(grant.id) or {}
        pid = pid if pid is not None else record.get("pid")
        pgid = pgid if pgid is not None else record.get("pgid")
        token = token or record.get("start_token")
    try:
        pid = int(pid) if pid else 0
    except (TypeError, ValueError):
        pid = 0
    try:
        pgid = int(pgid) if pgid else None
    except (TypeError, ValueError):
        pgid = None
    if pid <= 0:
        pid = 0
    if pgid is not None and pgid <= 0:
        pgid = None
    grant = replace(grant, pid=pid or None, pgid=pgid)
    def gone():
        if _pidfd is not None:
            return process_lifecycle.pidfd_exited(_pidfd)
        return _tree_gone(pid, pgid, reap=True)
    def send(sig):
        if _pidfd is not None:
            process_lifecycle.pidfd_signal(_pidfd, sig)
        else:
            _signal_tree(pid, pgid, sig)

    if not pid and not _group_present(pgid):
        outcome = _outcome_for(grant, dead=True, escalated=False)
        finish(grant, outcome)
        return outcome

    if recovered or require_identity:
        refusal = _ownership_gate(grant, pid, pgid, token)
        if refusal is not None:
            finish(grant, refusal)
            return refusal

    if IS_WINDOWS:
        process_lifecycle.taskkill_tree(pid)
        deadline = time.monotonic() + max(grace_s, 0.0)
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(_DEATH_POLL_S)
        outcome = _outcome_for(grant, dead=not pid_alive(pid), escalated=True)
        finish(grant, outcome)
        return outcome

    def regate(_sig):
        # The grace period is long enough for the pid to be freed and reissued;
        # a recovered claim must be re-proven before SIGKILL.
        if recovered or require_identity:
            return _ownership_gate(grant, pid, pgid, token)
        return None

    result = process_lifecycle.escalate(
        gone, send, steps=process_lifecycle.term_kill_steps(grace_s),
        poll_s=_DEATH_POLL_S, before_step=regate,
    )
    if result.refusal is not None:
        finish(grant, result.refusal)
        return result.refusal
    outcome = _outcome_for(grant, dead=result.dead, escalated=result.escalated)
    finish(grant, outcome)
    return outcome


def reap_record(record: Mapping[str, Any], *, grace_s: float = 2.0) -> ReleaseOutcome:
    """Tear down a grant known only by its durable record.

    The entry point for a reaper after a restart: the process that acquired the
    grant is gone, so there is no :class:`ContainmentGrant` in memory, only the
    row :func:`active_grants` returned. Reconstructs the minimum
    :func:`release` needs and goes through the same ownership gate — a record is
    a claim about a pid, and a reaper is exactly the caller that must not treat
    it as more than that.

    ``env`` is not reconstructed because it is never persisted (it is where
    credentials live) and teardown does not use it.
    """
    record = dict(record or {})
    spec = ContainmentSpec(
        workspace=record.get("workspace") or os.getcwd(),
        env={},
        wall_clock_s=int(record.get("wall_clock_s") or 1),
        required=frozenset(record.get("required") or ()),
    )
    grant = ContainmentGrant(
        id=str(record.get("id") or ""),
        mechanism=str(record.get("mechanism") or "none"),
        workspace=spec.workspace,
        enforced=frozenset(record.get("enforced") or ()),
        degraded=tuple(record.get("degraded") or ()),
        unenforced_required=tuple(record.get("unenforced_required") or ()),
        owner=str(record.get("owner") or "reaper"),
        mode=str(record.get("mode") or CONTAINMENT_MODE),
        spec=spec,
        external=bool(record.get("external")),
        # Left as None on purpose: release() then recovers pid, pgid and the
        # start token from the store itself and routes through the ownership
        # gate. Passing them here would mark the grant as held in-process and
        # skip the very check this path exists to apply.
        pid=None,
        pgid=None,
    )
    return release(grant, grace_s=grace_s)


async def _release_awaited(
    grant: ContainmentGrant,
    proc: "asyncio.subprocess.Process",
    *, grace_s: float = 2.0,
) -> ReleaseOutcome:
    try:
        return await _release_awaited_impl(grant, proc, grace_s=grace_s)
    finally:
        _close_process_handles(proc)


def _close_process_handles(proc) -> None:
    for name in ("_ody_info_read", "_ody_pidfd", "_ody_namespace_pidfd"):
        fd = getattr(proc, name, None)
        if fd is not None:
            os.close(fd)
            setattr(proc, name, None)


async def _release_awaited_impl(
    grant: ContainmentGrant,
    proc: "asyncio.subprocess.Process",
    *,
    grace_s: float = 2.0,
) -> ReleaseOutcome:
    """Teardown for a child this coroutine owns.

    Identical contract to :func:`release`, with one necessary difference: the
    leader is reaped through ``proc.wait()`` before the group is probed. An
    unreaped child is a zombie, a zombie is still a member of its process
    group, and so ``killpg(pgid, 0)`` would report survivors for a tree that
    has entirely exited — turning every timeout into a false "survivors"
    report.
    """
    writer = getattr(proc, "stdin", None)
    if writer is not None:
        writer.close()
        if hasattr(writer, "wait_closed"):
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (OSError, asyncio.TimeoutError):
                pass
    if IS_WINDOWS:
        return release(grant, grace_s=grace_s)

    if getattr(proc, "_ody_info_read", None) is not None:
        try:
            await asyncio.wait_for(_capture_namespace_identity(proc), timeout=1)
        except (ContainmentUnavailable, asyncio.TimeoutError):
            pass  # Setup never reached acknowledgment; no model code ran.
    pid, pgid = grant.pid, grant.pgid
    pidfd = getattr(proc, "_ody_pidfd", None)
    namespace_pid = getattr(proc, "_ody_namespace_pid", None)
    namespace_token = getattr(proc, "_ody_namespace_token", None)
    namespace_fd = getattr(proc, "_ody_namespace_pidfd", None)
    if namespace_pid:
        _update_record(grant.id, namespace_pid=namespace_pid, namespace_start_token=namespace_token)
    def namespace_gone():
        if namespace_fd is not None:
            return process_lifecycle.pidfd_exited(namespace_fd)
        if namespace_pid:
            if not pid_alive(namespace_pid):
                return True
            return process_ownership.verify(namespace_pid, namespace_token) in (
                process_ownership.GONE, process_ownership.FOREIGN,
            )
        return True
    def gone():
        if pidfd is not None:
            owner_gone = process_lifecycle.pidfd_exited(pidfd)
        elif grant.mechanism == "bubblewrap":
            owner_gone = proc.returncode is not None
        else:
            owner_gone = _tree_gone(pid, pgid)
        return owner_gone and namespace_gone()
    def send(sig):
        if pidfd is not None:
            process_lifecycle.pidfd_signal(pidfd, sig)
        elif proc.returncode is None or grant.mechanism != "bubblewrap":
            _signal_tree(pid, pgid, sig)
        if namespace_fd is not None:
            process_lifecycle.pidfd_signal(namespace_fd, sig)
        elif namespace_pid and process_ownership.verify(namespace_pid, namespace_token) == process_ownership.OWNED:
            _signal_tree(namespace_pid, None, sig)
    # No precheck: SIGTERM goes out first and the leader is reaped through
    # proc.wait() before any group probe, or its zombie reads as a survivor.
    result = await process_lifecycle.escalate_async(
        gone, send, steps=process_lifecycle.term_kill_steps(grace_s),
        wait=proc.wait, poll_s=_DEATH_POLL_S, precheck=False,
    )
    outcome = _outcome_for(grant, dead=result.dead, escalated=result.escalated)
    if not namespace_gone():
        outcome = replace(outcome, survivors=tuple(dict.fromkeys((*outcome.survivors, namespace_pid))))
    _finish_release(grant, outcome)
    return outcome


def _finish_release(grant: ContainmentGrant, outcome: ReleaseOutcome) -> None:
    if outcome.dead:
        _update_record(grant.id, released_at=time.time(), release=outcome.to_dict())
    else:
        # Deliberately NOT released: the record stays active so a reaper sees it
        # again. A record claiming teardown it did not achieve is the defect
        # this reverses.
        _update_record(grant.id, released_at=None, release=outcome.to_dict())
