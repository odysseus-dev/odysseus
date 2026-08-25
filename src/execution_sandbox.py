"""Linux process sandbox construction for model-requested code execution.

The application process remains the policy authority.  Model-supplied commands
are only appended after a fixed bubblewrap profile has removed the host
filesystem, inherited environment, network namespace, and ambient capabilities.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class SandboxUnavailable(RuntimeError):
    """Raised when the requested sandbox cannot be established safely."""


class SandboxNetworkProfile(str, Enum):
    """Server-owned network authority snapshotted when a process starts."""

    NETWORKLESS = "networkless"
    BROKERED_ONLY = "brokered_only"


def network_profile_for_internet_preference(enabled: bool) -> SandboxNetworkProfile:
    """Map the existing user Internet preference to process-boundary policy."""
    return (
        SandboxNetworkProfile.BROKERED_ONLY
        if enabled
        else SandboxNetworkProfile.NETWORKLESS
    )


def network_profile_from_snapshot(value: object) -> SandboxNetworkProfile:
    """Restore a persisted server snapshot without ever widening authority."""
    try:
        return SandboxNetworkProfile(value)
    except (TypeError, ValueError):
        return SandboxNetworkProfile.NETWORKLESS


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
_SYSTEM_WORKSPACE_ROOTS = frozenset(
    {
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib64",
        "/proc",
        "/root",
        "/run",
        "/sys",
        "/usr",
    }
)
_SENSITIVE_DIR_NAMES = frozenset(
    {
        ".agents",
        ".aws",
        ".azure",
        ".codex",
        ".cargo",
        ".docker",
        ".gnupg",
        ".kube",
        ".ssh",
    }
)
_SENSITIVE_WORKSPACE_ROOT_NAMES = frozenset(
    {
        ".aws",
        ".azure",
        ".config",
        ".docker",
        ".git",
        ".gnupg",
        ".kube",
        ".ssh",
    }
)
_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".bash_login",
        ".bash_profile",
        ".bash_logout",
        ".bashrc",
        ".cshrc",
        ".git-credentials",
        ".gitconfig",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".profile",
        ".pypirc",
        ".tcshrc",
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
_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")
_MOUNTINFO_PATH = "/proc/self/mountinfo"
_TRUSTED_BWRAP = "/usr/bin/bwrap"
_TRUSTED_SECCOMP_LAUNCHER = "/usr/local/libexec/odysseus-seccomp-launcher"
_TRUSTED_EGRESS_BROKER = "/usr/local/libexec/odysseus-egress-broker"
_TRUSTED_EGRESS_BRIDGE = "/usr/local/libexec/odysseus-egress-bridge"
_SANDBOX_PYTHON_VENV = "/run/odysseus-python-venv"
_BROKER_SOCKET = "/run/odysseus-egress/broker.sock"
_BROKER_PROXY_URL = "http://127.0.0.1:3128"
_CA_CERTIFICATE = "/etc/ssl/certs/ca-certificates.crt"
_SANDBOX_LIMITS = (
    "--as=4294967296",       # 4 GiB virtual address space per process
    "--core=0",
    "--cpu=3600",             # one hour of CPU time per process
    "--fsize=4294967296",     # 4 GiB per output file
    "--nofile=1024",
)


def _trusted_executable(path: str, description: str) -> str:
    """Require a fixed root-owned executable outside model-writable storage."""
    try:
        metadata = os.stat(path)
    except OSError as exc:
        raise SandboxUnavailable(
            f"Sandboxed agent execution requires the trusted {description} at {path}."
        ) from exc
    if (
        os.path.realpath(path) != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode
        & (stat.S_ISUID | stat.S_ISGID | stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(path, os.X_OK)
    ):
        raise SandboxUnavailable(
            f"Trusted {description} is not a root-owned, non-setuid, "
            f"read-only executable at {path}."
        )
    return path


def _trusted_python_helper(path: str, description: str) -> str:
    """Require an isolated, fixed-interpreter trusted Python entry point."""
    helper = _trusted_executable(path, description)
    try:
        with open(helper, "rb") as stream:
            first_line = stream.readline(256).decode("ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise SandboxUnavailable(
            f"Trusted {description} has an invalid interpreter declaration."
        ) from exc
    fields = first_line.removeprefix("#!").split()
    if (
        not first_line.startswith("#!")
        or len(fields) != 2
        or fields[1] != "-I"
        or not fields[0].startswith("/usr/")
    ):
        raise SandboxUnavailable(
            f"Trusted {description} must use an isolated absolute Python interpreter."
        )
    _trusted_executable(fields[0], f"{description} Python interpreter")
    return helper


def _bubblewrap_binary() -> str:
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable(
            "Sandboxed agent execution requires Linux with bubblewrap."
        )
    return _trusted_executable(_TRUSTED_BWRAP, "Bubblewrap binary")


def _seccomp_launcher_binary() -> str:
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable(
            "Sandboxed agent execution requires Linux with the trusted seccomp launcher."
        )
    return _trusted_executable(_TRUSTED_SECCOMP_LAUNCHER, "seccomp launcher")


def _egress_broker_binary() -> str:
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable(
            "Brokered Internet requires Linux with the trusted egress broker."
        )
    return _trusted_python_helper(_TRUSTED_EGRESS_BROKER, "egress broker")


def _egress_bridge_binary() -> str:
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable(
            "Brokered Internet requires Linux with the trusted egress bridge."
        )
    return _trusted_python_helper(_TRUSTED_EGRESS_BRIDGE, "egress bridge")


def _login_home_roots() -> set[str]:
    """Return real login-home roots without making account lookup mandatory."""
    homes = {
        os.path.realpath(path)
        for path in (os.path.expanduser("~"), os.environ.get("HOME", ""))
        if path
    }
    try:
        import pwd

        homes.update(
            os.path.realpath(entry.pw_dir)
            for entry in pwd.getpwall()
            if entry.pw_dir and os.path.isabs(entry.pw_dir)
        )
    except (ImportError, KeyError, OSError):
        pass
    return homes


def _normalized_workspace(workspace: str) -> str:
    if not isinstance(workspace, str) or not workspace.strip():
        raise SandboxUnavailable("Sandboxed execution requires a workspace.")
    resolved = os.path.realpath(os.path.expanduser(workspace))
    from src.constants import AGENT_WORKSPACE_DIR

    managed_workspace = os.path.realpath(AGENT_WORKSPACE_DIR)
    exposes_login_home = (
        not _is_within(resolved, managed_workspace)
        and any(
            resolved == home or _is_within(home, resolved)
            for home in _login_home_roots()
        )
    )
    sensitive_root = Path(resolved).name.casefold() in _SENSITIVE_WORKSPACE_ROOT_NAMES
    if sensitive_root:
        raise SandboxUnavailable(
            f"Refusing sensitive sandbox workspace root: {resolved}"
        )
    if (
        resolved in _BROAD_WORKSPACE_ROOTS
        or exposes_login_home
        or os.path.dirname(resolved) == resolved
        or any(_is_within(resolved, root) for root in _SYSTEM_WORKSPACE_ROOTS)
    ):
        raise SandboxUnavailable(
            f"Refusing broad or login-profile sandbox workspace: {resolved}"
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


def _reject_nested_workspace_mounts(workspace: str) -> None:
    """Reject mount points that a recursive workspace bind would carry in."""
    try:
        with open(
            _MOUNTINFO_PATH,
            encoding="utf-8",
            errors="surrogateescape",
        ) as stream:
            entries = list(stream)
    except OSError as exc:
        raise SandboxUnavailable(
            "Unable to verify sandbox workspace mount boundaries."
        ) from exc

    for entry in entries:
        fields = entry.split()
        if len(fields) < 5:
            raise SandboxUnavailable(
                "Unable to verify sandbox workspace mount boundaries."
            )
        mount_point = _MOUNTINFO_ESCAPE.sub(
            lambda match: chr(int(match.group(1), 8)),
            fields[4],
        )
        resolved_mount = os.path.realpath(mount_point)
        if resolved_mount != workspace and _is_within(resolved_mount, workspace):
            relative = os.path.relpath(resolved_mount, workspace).replace(os.sep, "/")
            raise SandboxUnavailable(
                f"Sandbox workspace contains a nested mount: {relative}"
            )


def _workspace_overlays(
    workspace: str,
    *,
    excluded_roots: Sequence[str] = (),
) -> list[str]:
    """Return mounts that protect repository metadata and credential paths."""
    _reject_nested_workspace_mounts(workspace)
    args: list[str] = []
    scanned = 0
    for root, dirs, files in os.walk(workspace, followlinks=False):
        dirs.sort()
        files.sort()
        scanned += len(dirs) + len(files)
        if scanned > _MAX_WORKSPACE_SCAN_ENTRIES:
            raise SandboxUnavailable(
                "Workspace is too large to verify credential-path overlays "
                "safely; narrow the workspace before running code."
            )

        retained_dirs: list[str] = []
        for name in dirs:
            path = os.path.join(root, name)
            folded = name.casefold()
            relative = os.path.relpath(path, workspace).replace(os.sep, "/").casefold()
            is_symlink = os.path.islink(path)
            if is_symlink and (
                folded == ".git"
                or folded in _SENSITIVE_DIR_NAMES
                or relative == ".config/gh"
            ):
                raise SandboxUnavailable(
                    f"Sensitive sandbox path cannot be a symlink: {relative}"
                )
            resolved_path = os.path.realpath(path)
            if any(
                resolved_path == excluded or _is_within(resolved_path, excluded)
                for excluded in excluded_roots
            ):
                continue
            if folded == ".git":
                args.extend(("--ro-bind", path, path))
            elif folded in _SENSITIVE_DIR_NAMES or relative == ".config/gh":
                args.extend(("--tmpfs", path))
            else:
                retained_dirs.append(name)
        dirs[:] = retained_dirs

        for name in files:
            path = os.path.join(root, name)
            relative = os.path.relpath(path, workspace).replace(os.sep, "/")
            try:
                metadata = os.lstat(path)
            except OSError as exc:
                raise SandboxUnavailable(
                    f"Unable to verify sandbox workspace entry: {relative}"
                ) from exc
            if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
                raise SandboxUnavailable(
                    f"Sandbox workspace contains an unsupported special file: {relative}"
                )
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
                raise SandboxUnavailable(
                    f"Sandbox workspace contains a hard-linked file: {relative}"
                )
            if name.casefold() == ".git" and os.path.islink(path):
                raise SandboxUnavailable(
                    "Sensitive sandbox path cannot be a symlink: .git"
                )
            if name.casefold() == ".git":
                args.extend(("--ro-bind", path, path))
            elif _is_sensitive_file(name):
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
        APP_DB,
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

    protected_database_paths = {os.path.realpath(APP_DB)}
    configured_database = os.environ.get("DATABASE_URL", "").strip()
    if configured_database:
        from src.runtime_paths import get_app_root
        from src.sqlite_paths import resolve_sqlite_db_path

        database_path = resolve_sqlite_db_path(
            configured_database,
            app_root=get_app_root(),
        )
        if database_path is not None:
            protected_database_paths.add(database_path)

    for database_path in sorted(protected_database_paths):
        if _is_within(database_path, workspace) and not any(
            _is_within(database_path, hidden) for hidden in hidden_roots
        ):
            raise SandboxUnavailable(
                "The selected workspace contains an Odysseus SQLite database. "
                "Choose a narrower workspace."
            )
    return args, hidden_roots


def _system_python_executable() -> str:
    """Choose an interpreter path covered by the read-only /usr runtime mount."""
    current = os.path.realpath(sys.executable or "")
    if current.startswith("/usr/") and os.path.isfile(current):
        return current
    for candidate in ("/usr/local/bin/python3", "/usr/bin/python3"):
        if os.path.isfile(candidate):
            return candidate
    raise SandboxUnavailable("No system Python interpreter is available in /usr.")


def _native_virtualenv_runtime() -> str | None:
    """Return the canonical trusted native venv root, when one is active."""
    if sys.prefix == sys.base_prefix:
        return None

    prefix = os.path.realpath(os.path.abspath(sys.prefix or ""))
    config = os.path.join(prefix, "pyvenv.cfg")
    resolved_config = os.path.realpath(config)
    venv_python = os.path.join(prefix, "bin", "python")
    resolved_executable = os.path.realpath(venv_python)
    try:
        prefix_metadata = os.stat(prefix)
        config_metadata = os.lstat(config)
        executable_metadata = os.stat(resolved_executable)
    except OSError:
        prefix_metadata = None
        config_metadata = None
        executable_metadata = None
    if (
        os.path.basename(prefix) not in {"venv", ".venv"}
        or prefix_metadata is None
        or not stat.S_ISDIR(prefix_metadata.st_mode)
        or prefix_metadata.st_uid not in {0, os.geteuid()}
        or prefix_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not resolved_executable.startswith("/usr/")
        or executable_metadata is None
        or not stat.S_ISREG(executable_metadata.st_mode)
        or executable_metadata.st_uid != 0
        or executable_metadata.st_mode
        & (stat.S_ISUID | stat.S_ISGID | stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(resolved_executable, os.X_OK)
        or config_metadata is None
        or not stat.S_ISREG(config_metadata.st_mode)
        or config_metadata.st_uid not in {0, os.geteuid()}
        or config_metadata.st_mode
        & (stat.S_ISUID | stat.S_ISGID | stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(config, os.R_OK)
        or resolved_config != config
        or not _is_within(resolved_config, prefix)
    ):
        raise SandboxUnavailable(
            "The running Python virtualenv cannot be represented safely inside "
            "the Sandbox. Use a standard `venv` or `.venv` backed by a trusted "
            "system Python under /usr."
        )
    return prefix


def sandbox_python_executable() -> str:
    """Return the system interpreter exposed by the base Sandbox runtime."""
    return _system_python_executable()


def sandbox_python_command(
    arguments: Sequence[str],
    *,
    workspace: str,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
) -> list[str]:
    """Build a Sandbox command using the application's Python environment."""
    if not all(isinstance(part, str) for part in arguments):
        raise SandboxUnavailable("Sandbox Python arguments must be strings.")
    runtime = _native_virtualenv_runtime()
    if runtime is None:
        executable = _system_python_executable()
        readonly_python_venv = None
    else:
        executable = f"{_SANDBOX_PYTHON_VENV}/bin/python"
        readonly_python_venv = runtime
    return sandbox_command(
        [executable, *arguments],
        workspace=workspace,
        readonly_python_venv=readonly_python_venv,
        network_profile=network_profile,
    )


def sandbox_command(
    command: Sequence[str],
    *,
    workspace: str,
    readonly_files: Mapping[str, str] | None = None,
    readonly_python_venv: str | None = None,
    extra_environment: Mapping[str, str] | None = None,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
) -> list[str]:
    """Build a positive-mount bubblewrap command.

    `readonly_files` maps trusted server-generated host files to absolute paths
    inside the sandbox. `readonly_python_venv` accepts only the running trusted
    native venv and binds it read-only at one fixed destination. Network
    authority is a server-owned launch snapshot. Raw container networking is
    never available in Sandbox mode.
    """
    if not command or not all(isinstance(part, str) for part in command):
        raise SandboxUnavailable("Sandbox command must be a non-empty argv list.")

    if not isinstance(network_profile, SandboxNetworkProfile):
        raise SandboxUnavailable("Invalid server-owned sandbox network profile.")
    launcher = _seccomp_launcher_binary()
    binary = _bubblewrap_binary()
    broker = None
    bridge = None
    if network_profile is SandboxNetworkProfile.BROKERED_ONLY:
        broker = _egress_broker_binary()
        bridge = _egress_bridge_binary()
        if not os.path.isfile(_CA_CERTIFICATE):
            raise SandboxUnavailable(
                "Brokered Internet requires the system CA certificate bundle."
            )
    root = _normalized_workspace(workspace)
    trusted_paths = [launcher, binary]
    if broker is not None and bridge is not None:
        trusted_paths.extend((broker, bridge))
    if any(_is_within(path, root) for path in trusted_paths):
        raise SandboxUnavailable(
            "Trusted sandbox installation overlaps the selected workspace."
        )
    if not os.path.isfile("/usr/bin/prlimit"):
        raise SandboxUnavailable(
            "Sandboxed agent execution requires `/usr/bin/prlimit`."
        )
    args = [launcher, binary]
    if broker is not None:
        args.insert(0, broker)
    args.extend(
        [
            "--unshare-user",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--unshare-cgroup",
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
    )
    if os.path.exists("/usr/lib64"):
        args.extend(("--symlink", "usr/lib64", "/lib64"))
    args.extend(
        (
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/tmp/odysseus-home",
        )
    )

    args.extend(_directory_creation_args(root))
    args.extend(("--bind", root, root))
    if os.path.isfile(_CA_CERTIFICATE):
        args.extend(_directory_creation_args(_CA_CERTIFICATE, include_leaf=False))
        args.extend(("--ro-bind", _CA_CERTIFICATE, _CA_CERTIFICATE))
    data_overlays, hidden_data_roots = _odysseus_data_overlays(root)
    args.extend(data_overlays)
    args.extend(_workspace_overlays(root, excluded_roots=hidden_data_roots))

    if readonly_python_venv is not None:
        source = os.path.realpath(readonly_python_venv)
        if source == root or _is_within(source, root):
            raise SandboxUnavailable(
                "The trusted Python virtualenv cannot be inside the writable workspace."
            )
        expected = _native_virtualenv_runtime()
        if expected is None or source != expected:
            raise SandboxUnavailable("Invalid trusted Python virtualenv mount source.")
        _reject_nested_workspace_mounts(source)
        args.extend(_directory_creation_args(_SANDBOX_PYTHON_VENV))
        args.extend(("--ro-bind", source, _SANDBOX_PYTHON_VENV))

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
        "SSL_CERT_FILE": _CA_CERTIFICATE,
        "TERM": "xterm-256color",
        "TMPDIR": "/tmp",
    }
    if network_profile is SandboxNetworkProfile.BROKERED_ONLY:
        environment.update(
            {
                "HTTP_PROXY": _BROKER_PROXY_URL,
                "HTTPS_PROXY": _BROKER_PROXY_URL,
                "http_proxy": _BROKER_PROXY_URL,
                "https_proxy": _BROKER_PROXY_URL,
            }
        )
    for name, value in (extra_environment or {}).items():
        if name in {"COLUMNS", "LINES", "TERM"} and isinstance(value, str):
            environment[name] = value[:80]
    for name, value in environment.items():
        args.extend(("--setenv", name, value))

    args.extend(("--chdir", root, "--"))
    if bridge is not None:
        args.extend((bridge, _BROKER_SOCKET, "--"))
    args.extend(process_limited_command(command))
    return args


def full_access_command(
    command: Sequence[str],
    *,
    working_directory: str,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
) -> list[str]:
    """Build the explicit full-filesystem profile with retained network policy.

    This profile grants the payload the same filesystem view and permissions as
    the Odysseus service user. It still uses a private network namespace: no
    Internet by default, or trusted brokered HTTP(S) when explicitly enabled.
    It is not an unsandboxed fallback and therefore remains unavailable when the
    minimum Bubblewrap/network boundary cannot be established.
    """
    if not command or not all(isinstance(part, str) for part in command):
        raise SandboxUnavailable("Full Access command must be a non-empty argv list.")
    if not isinstance(network_profile, SandboxNetworkProfile):
        raise SandboxUnavailable("Invalid server-owned sandbox network profile.")

    cwd = os.path.realpath(os.path.expanduser(working_directory or "."))
    if not os.path.isdir(cwd):
        raise SandboxUnavailable("Full Access working directory is unavailable.")

    launcher = _seccomp_launcher_binary()
    binary = _bubblewrap_binary()
    broker = None
    bridge = None
    if network_profile is SandboxNetworkProfile.BROKERED_ONLY:
        broker = _egress_broker_binary()
        bridge = _egress_bridge_binary()
        if not os.path.isfile(_CA_CERTIFICATE):
            raise SandboxUnavailable(
                "Brokered Internet requires the system CA certificate bundle."
            )

    args = [launcher, binary]
    if broker is not None:
        args.insert(0, broker)
    args.extend(
        (
            "--unshare-user",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--unshare-cgroup",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--cap-drop",
            "ALL",
            "--bind",
            "/",
            "/",
            "--proc",
            "/proc",
        )
    )

    environment = {
        "COLUMNS": "120",
        "HOME": os.path.expanduser("~"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "LINES": "40",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "TERM": os.environ.get("TERM", "xterm-256color"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    if os.path.isfile(_CA_CERTIFICATE):
        environment["SSL_CERT_FILE"] = _CA_CERTIFICATE
    if network_profile is SandboxNetworkProfile.BROKERED_ONLY:
        environment.update(
            {
                "HTTP_PROXY": _BROKER_PROXY_URL,
                "HTTPS_PROXY": _BROKER_PROXY_URL,
                "http_proxy": _BROKER_PROXY_URL,
                "https_proxy": _BROKER_PROXY_URL,
            }
        )
    for name, value in environment.items():
        args.extend(("--setenv", name, value))

    args.extend(("--chdir", cwd, "--"))
    if bridge is not None:
        args.extend((bridge, _BROKER_SOCKET, "--"))
    args.extend(process_limited_command(command))
    return args


def process_limited_command(command: Sequence[str]) -> list[str]:
    """Apply generous per-process ceilings without claiming tree containment."""
    if not command or not all(isinstance(part, str) for part in command):
        raise SandboxUnavailable("Process command must be a non-empty argv list.")
    if not os.path.isfile("/usr/bin/prlimit"):
        raise SandboxUnavailable(
            "Agent process execution requires `/usr/bin/prlimit`."
        )
    return ["/usr/bin/prlimit", *_SANDBOX_LIMITS, "--", *command]


def environment_for_sandbox_launcher() -> dict[str, str]:
    """Minimal environment for the trusted bubblewrap launcher itself."""
    return {}
