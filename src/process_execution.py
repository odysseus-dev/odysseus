"""Server-owned process authority and capability state.

Sandbox is the default on every start. Full Access is a transient administrator
choice for trusted work that grants the process the Odysseus service user's
filesystem authority while retaining the sandbox's private-network policy. It
is never selected automatically after a capability failure and is not persisted
across application restarts.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


FULL_ACCESS_CONFIRMATION = "ENABLE FULL ACCESS"
FULL_ACCESS_WARNING = (
    "Full Access lets Bash, Python, and detached process tools read or modify "
    "everything available to the Odysseus operating-system user. In Docker "
    "that includes the container and mounted volumes; natively it includes the "
    "service user's accessible files. Process Internet remains networkless by "
    "default and, when enabled, is limited to the trusted HTTP(S) broker. "
    "New process launches reset to Sandbox when Odysseus restarts. Any "
    "already-running Full Access process retains its launch-time authority "
    "until it exits or is killed. Enable it only for trusted tasks."
)
_SANDBOX_STATUS_TTL_SECONDS = 30.0
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: tuple[float, "ProcessCapability"] | None = None
_MODE_LOCK = threading.Lock()
_MODE = None


class ProcessExecutionMode(str, Enum):
    SANDBOX = "sandbox"
    FULL_ACCESS = "full_access"


@dataclass(frozen=True)
class ProfileCapability:
    networkless: bool
    networkless_reason: str
    brokered: bool
    brokered_reason: str

    def supports(self, network_profile: object) -> bool:
        from src.execution_sandbox import SandboxNetworkProfile

        if network_profile is SandboxNetworkProfile.BROKERED_ONLY:
            return self.networkless and self.brokered
        return self.networkless

    def reason_for(self, network_profile: object) -> str:
        from src.execution_sandbox import SandboxNetworkProfile

        if network_profile is SandboxNetworkProfile.BROKERED_ONLY:
            return self.brokered_reason or self.networkless_reason
        return self.networkless_reason


@dataclass(frozen=True)
class ProcessCapability:
    sandbox: ProfileCapability
    full_access: ProfileCapability
    checked_at: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def for_mode(self, mode: ProcessExecutionMode) -> ProfileCapability:
        if mode is ProcessExecutionMode.FULL_ACCESS:
            return self.full_access
        return self.sandbox


def process_execution_mode_from_value(value: object) -> ProcessExecutionMode:
    try:
        return ProcessExecutionMode(str(value or "").strip().lower())
    except ValueError:
        return ProcessExecutionMode.SANDBOX


def configured_process_execution_mode() -> ProcessExecutionMode:
    global _MODE

    with _MODE_LOCK:
        if _MODE is None:
            _MODE = ProcessExecutionMode.SANDBOX
        return _MODE


def set_process_execution_mode(
    mode: ProcessExecutionMode,
    *,
    confirmation: str = "",
) -> ProcessExecutionMode:
    global _MODE

    if not isinstance(mode, ProcessExecutionMode):
        raise ValueError("invalid process execution mode")
    if (
        mode is ProcessExecutionMode.FULL_ACCESS
        and confirmation != FULL_ACCESS_CONFIRMATION
    ):
        raise ValueError("Full Access confirmation did not match")
    with _MODE_LOCK:
        _MODE = mode
    return mode


def reset_process_execution_mode() -> None:
    global _MODE

    with _MODE_LOCK:
        _MODE = ProcessExecutionMode.SANDBOX


def _public_probe_reason(stderr: str, fallback: str) -> str:
    detail = (stderr or "").strip().splitlines()
    if detail:
        first = detail[0].strip()
        if first.startswith(
            (
                "bwrap:",
                "odysseus-seccomp-launcher:",
                "odysseus-egress-broker:",
                "odysseus-egress-bridge:",
            )
        ):
            return first[:500]
    return fallback[:500]


def _probe_command(argv: list[str], workspace: str) -> tuple[bool, str]:
    from src.execution_sandbox import environment_for_sandbox_launcher

    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            env=environment_for_sandbox_launcher(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0:
            return True, ""
        return False, _public_probe_reason(
            completed.stderr,
            "The process boundary could not be established on this host.",
        )
    except subprocess.TimeoutExpired:
        return False, "The process capability probe timed out."
    except Exception as exc:
        return False, str(exc)[:500]


def _probe_profile(
    workspace: str,
    network_profile: object,
    *,
    full_access: bool,
) -> tuple[bool, str]:
    from src.execution_sandbox import full_access_command, sandbox_command

    try:
        if full_access:
            argv = full_access_command(
                ["/bin/true"],
                working_directory=workspace,
                network_profile=network_profile,
            )
        else:
            argv = sandbox_command(
                ["/bin/true"],
                workspace=workspace,
                network_profile=network_profile,
            )
    except Exception as exc:
        return False, str(exc)[:500]
    return _probe_command(argv, workspace)


def _probe_one_mode(workspace: str, *, full_access: bool) -> ProfileCapability:
    from src.execution_sandbox import SandboxNetworkProfile

    networkless, networkless_reason = _probe_profile(
        workspace,
        SandboxNetworkProfile.NETWORKLESS,
        full_access=full_access,
    )
    if networkless:
        brokered, brokered_reason = _probe_profile(
            workspace,
            SandboxNetworkProfile.BROKERED_ONLY,
            full_access=full_access,
        )
    else:
        brokered = False
        brokered_reason = networkless_reason
    return ProfileCapability(
        networkless,
        networkless_reason,
        brokered,
        brokered_reason,
    )


def _probe_process_capability() -> ProcessCapability:
    from src.constants import AGENT_WORKSPACE_DIR

    checked_at = time.time()
    probe_parent = Path(AGENT_WORKSPACE_DIR)
    try:
        probe_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".sandbox-capability-",
            dir=str(probe_parent),
        ) as workspace:
            sandbox = _probe_one_mode(workspace, full_access=False)
            full_access = _probe_one_mode(workspace, full_access=True)
    except Exception as exc:
        reason = str(exc)[:500]
        unavailable = ProfileCapability(False, reason, False, reason)
        sandbox = unavailable
        full_access = unavailable
    return ProcessCapability(sandbox, full_access, checked_at)


def process_capability(*, refresh: bool = False) -> ProcessCapability:
    global _STATUS_CACHE

    now = time.monotonic()
    with _STATUS_LOCK:
        if (
            not refresh
            and _STATUS_CACHE is not None
            and now - _STATUS_CACHE[0] < _SANDBOX_STATUS_TTL_SECONDS
        ):
            return _STATUS_CACHE[1]
        status = _probe_process_capability()
        _STATUS_CACHE = (now, status)
        return status


def clear_process_capability_cache() -> None:
    global _STATUS_CACHE

    with _STATUS_LOCK:
        _STATUS_CACHE = None


def blocked_process_result(
    tool: str,
    mode: ProcessExecutionMode,
    reason: str,
) -> dict[str, object]:
    return {
        "error": (
            f"{tool}: {mode.value.replace('_', ' ').title()} process boundary "
            f"unavailable: {reason} Process tools remain blocked; Odysseus "
            "never downgrades execution authority automatically."
        ),
        "exit_code": 1,
        "blocked": True,
        "execution_mode": mode.value,
    }
