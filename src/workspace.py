"""Workspace directory resolution for shell and file tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.settings import get_setting

ENV_WORKSPACE_DIR = "ODYSSEUS_WORKSPACE_DIR"
ENV_APP_DIR = "ODYSSEUS_APP_DIR"
APP_DIR = Path(__file__).resolve().parent.parent


class WorkspaceError(ValueError):
    """Raised when a configured workspace path cannot be used."""


def resolve_workspace_dir(cwd: Optional[str] = None) -> str:
    """Resolve the effective workspace directory.

    Precedence:
      1. explicit API/request cwd
      2. persisted workspace_dir setting
      3. ODYSSEUS_WORKSPACE_DIR environment variable
      4. the process user's home directory
    """
    raw = (cwd or "").strip()
    if not raw:
        raw = str(get_setting("workspace_dir", "") or "").strip()
    if not raw:
        raw = (os.environ.get(ENV_WORKSPACE_DIR) or "").strip()
    if not raw:
        return str(Path.home())

    path = Path(raw).expanduser()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise WorkspaceError(f"Workspace directory is not accessible: {path}") from exc
    if not resolved.exists():
        raise WorkspaceError(f"Workspace directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise WorkspaceError(f"Workspace path is not a directory: {resolved}")
    return str(resolved)


def resolve_workspace_path(path: str) -> str:
    """Resolve a tool path, anchoring relative paths inside the workspace."""
    raw = (path or "").strip()
    if not raw:
        return ""
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str((Path(resolve_workspace_dir()) / expanded).resolve())


def workspace_subprocess_env(
    workdir: str,
    base_env: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Build the environment for subprocesses launched from a workspace.

    User-facing shell/tool commands run from the configured workspace instead
    of the app root. Keep Odysseus' own CLIs discoverable from there by adding
    the repo scripts directory to PATH and exposing the app root explicitly.
    """
    env = dict(base_env or os.environ)
    scripts_dir = str(APP_DIR / "scripts")
    local_bin_dir = str(APP_DIR / ".local" / "bin")
    existing_path = env.get("PATH", "")
    path_parts = [p for p in existing_path.split(os.pathsep) if p]
    merged: list[str] = []
    for path in (scripts_dir, local_bin_dir, *path_parts):
        if path and path not in merged:
            merged.append(path)
    env["PATH"] = os.pathsep.join(merged)
    env[ENV_WORKSPACE_DIR] = workdir
    env[ENV_APP_DIR] = str(APP_DIR)
    return env
