"""Linux process sandbox construction for model-requested code execution.

The application process remains the policy authority.  Model-supplied commands
are only appended after a fixed bubblewrap profile has removed the host
filesystem, inherited environment, network namespace, and ambient capabilities.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping, Sequence


class SandboxUnavailable(RuntimeError):
    """Raised when the requested sandbox cannot be established safely."""


_BROAD_WORKSPACE_ROOTS = frozenset(
    {
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/lib64",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/srv",
        "/sys",
        "/tmp",
        "/usr",
        "/var",
    }
)
_SENSITIVE_DIR_NAMES = frozenset(
    {
        ".agents",
        ".aws",
        ".azure",
        ".codex",
        ".docker",
        ".gnupg",
        ".kube",
        ".ssh",
    }
)
_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".bash_profile",
        ".bashrc",
        ".git-credentials",
        ".gitconfig",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".zprofile",
        ".zshenv",
        ".zshrc",
        "authorized_keys",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
_MAX_WORKSPACE_SCAN_ENTRIES = 100_000
_SANDBOX_LIMITS = (
    "--as=4294967296",
    "--core=0",
    "--cpu=900",
    "--fsize=1073741824",
    "--nofile=256",
    "--nproc=256",
)


def _bubblewrap_binary() -> str:
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable(
            "Sandboxed agent execution requires Linux with bubblewrap."
        )
    binary = shutil.which("bwrap")
    if not binary:
        raise SandboxUnavailable(
            "Sandboxed agent execution is unavailable because bubblewrap "
            "(`bwrap`) is not installed."
        )
    return os.path.realpath(binary)


def _normalized_workspace(workspace: str) -> str:
    if not isinstance(workspace, str) or not workspace.strip():
        raise SandboxUnavailable("Sandboxed execution requires a workspace.")
    resolved = os.path.realpath(os.path.expanduser(workspace))
    if resolved in _BROAD_WORKSPACE_ROOTS or os.path.dirname(resolved) == resolved:
        raise SandboxUnavailable(
            f"Refusing broad sandbox workspace: {resolved}"
        )
    try:
        Path(resolved).mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise SandboxUnavailable(
            f"Unable to prepare sandbox workspace: {exc}"
        ) from exc
    if not os.path.isdir(resolved):
        raise SandboxUnavailable("Sandbox workspace is not a directory.")
    return resolved


def _directory_creation_args(path: str, *, include_leaf: bool = True) -> list[str]:
    target = Path(path)
    parts = target.parts
    if not parts or parts[0] != os.sep:
        raise SandboxUnavailable(f"Sandbox mount path must be absolute: {path}")
    limit = len(parts) if include_leaf else len(parts) - 1
    args: list[str] = []
    current = Path(os.sep)
    for part in parts[1:limit]:
        current /= part
        args.extend(("--dir", str(current)))
    return args


def _is_sensitive_file(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in _SENSITIVE_FILE_NAMES
        or folded == ".env"
        or folded.startswith(".env.")
    )


def _workspace_overlays(
    workspace: str,
    *,
    excluded_roots: Sequence[str] = (),
) -> list[str]:
    """Return mounts that protect repository metadata and credential paths."""
    args: list[str] = []
    scanned = 0
    for root, dirs, files in os.walk(workspace, followlinks=False):
        scanned += len(dirs) + len(files)
        if scanned > _MAX_WORKSPACE_SCAN_ENTRIES:
            raise SandboxUnavailable(
                "Workspace is too large to verify credential-path overlays "
                "safely; narrow the workspace before running code."
            )

        retained_dirs: list[str] = []
        for name in dirs:
            path = os.path.join(root, name)
            resolved_path = os.path.realpath(path)
            if any(
                resolved_path == excluded or _is_within(resolved_path, excluded)
                for excluded in excluded_roots
            ):
                continue
            folded = name.casefold()
            if folded == ".git":
                args.extend(("--ro-bind", path, path))
            elif folded in _SENSITIVE_DIR_NAMES:
                args.extend(("--tmpfs", path))
            else:
                retained_dirs.append(name)
        dirs[:] = retained_dirs

        for name in files:
            if _is_sensitive_file(name):
                path = os.path.join(root, name)
                args.extend(("--ro-bind", "/dev/null", path))
    return args


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except (TypeError, ValueError):
        return False


def _odysseus_data_overlays(workspace: str) -> tuple[list[str], list[str]]:
    """Hide application-owned stores even inside a broader selected workspace."""
    from src.constants import (
        AGENT_WORKSPACE_DIR,
        DATA_DIR,
        LOGS_DIR,
        MAIL_ATTACHMENTS_DIR,
    )

    agent_workspace = os.path.realpath(AGENT_WORKSPACE_DIR)
    protected_roots = {
        os.path.realpath(DATA_DIR),
        os.path.realpath(LOGS_DIR),
        os.path.realpath(MAIL_ATTACHMENTS_DIR),
    }
    top_level_roots = {
        candidate
        for candidate in protected_roots
        if not any(
            candidate != other and _is_within(candidate, other)
            for other in protected_roots
        )
    }
    args: list[str] = []
    hidden_roots: list[str] = []
    for protected in sorted(top_level_roots):
        if _is_within(workspace, protected):
            if _is_within(workspace, agent_workspace):
                continue
            raise SandboxUnavailable(
                "Odysseus application data cannot be selected as an agent "
                "process workspace."
            )
        if _is_within(protected, workspace) and os.path.isdir(protected):
            args.extend(("--tmpfs", protected))
            hidden_roots.append(protected)
    return args, hidden_roots


def sandbox_python_executable() -> str:
    """Choose an interpreter path covered by the read-only /usr runtime mount."""
    current = os.path.realpath(sys.executable or "")
    if current.startswith("/usr/") and os.path.isfile(current):
        return current
    for candidate in ("/usr/local/bin/python3", "/usr/bin/python3"):
        if os.path.isfile(candidate):
            return candidate
    raise SandboxUnavailable("No system Python interpreter is available in /usr.")


def sandbox_command(
    command: Sequence[str],
    *,
    workspace: str,
    readonly_files: Mapping[str, str] | None = None,
    extra_environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Build a positive-mount, networkless bubblewrap command.

    `readonly_files` maps host source files to absolute paths inside the
    sandbox.  It is intended for server-generated command files, never broad
    directories.
    """
    if not command or not all(isinstance(part, str) for part in command):
        raise SandboxUnavailable("Sandbox command must be a non-empty argv list.")

    binary = _bubblewrap_binary()
    root = _normalized_workspace(workspace)
    if not os.path.isfile("/usr/bin/prlimit"):
        raise SandboxUnavailable(
            "Sandboxed agent execution requires `/usr/bin/prlimit`."
        )
    args = [
        binary,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
    ]
    if os.path.exists("/usr/lib64"):
        args.extend(("--symlink", "usr/lib64", "/lib64"))
    args.extend(
        (
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/tmp/odysseus-home",
        )
    )

    args.extend(_directory_creation_args(root))
    args.extend(("--bind", root, root))
    data_overlays, hidden_data_roots = _odysseus_data_overlays(root)
    args.extend(data_overlays)
    args.extend(_workspace_overlays(root, excluded_roots=hidden_data_roots))

    for source, destination in (readonly_files or {}).items():
        source_path = os.path.realpath(source)
        if not os.path.isfile(source_path):
            raise SandboxUnavailable(
                f"Sandbox read-only input is not a file: {source}"
            )
        if not isinstance(destination, str) or not destination.startswith("/"):
            raise SandboxUnavailable(
                "Sandbox read-only destinations must be absolute paths."
            )
        args.extend(_directory_creation_args(destination, include_leaf=False))
        args.extend(("--ro-bind", source_path, destination))

    environment = {
        "COLUMNS": "120",
        "HOME": "/tmp/odysseus-home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LINES": "40",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "TERM": "xterm-256color",
        "TMPDIR": "/tmp",
    }
    for name, value in (extra_environment or {}).items():
        if name in {"COLUMNS", "LINES", "TERM"} and isinstance(value, str):
            environment[name] = value[:80]
    for name, value in environment.items():
        args.extend(("--setenv", name, value))

    args.extend(("--chdir", root, "--", "/usr/bin/prlimit"))
    args.extend(_SANDBOX_LIMITS)
    args.extend(("--",))
    args.extend(command)
    return args


def environment_for_sandbox_launcher() -> dict[str, str]:
    """Minimal environment for the trusted bubblewrap launcher itself."""
    return {}
