"""Workspace directory resolution for shell and file tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.settings import get_setting

ENV_WORKSPACE_DIR = "ODYSSEUS_WORKSPACE_DIR"


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
