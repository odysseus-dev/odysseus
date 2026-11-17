"""Workspace path mapping for host-driven clients and Docker backends."""

from __future__ import annotations

import os
from pathlib import PurePosixPath


def _clean_path(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        return ""
    return os.path.abspath(os.path.expanduser(text))


def _path_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    mapping = os.environ.get("ODYSSEUS_WORKSPACE_MOUNTS", "")
    for item in mapping.replace(";", ",").split(","):
        if not item.strip() or "=" not in item:
            continue
        host, container = item.split("=", 1)
        host_path = _clean_path(host)
        container_path = _clean_path(container)
        if host_path and container_path:
            pairs.append((host_path, container_path))

    host_root = _clean_path(os.environ.get("ODYSSEUS_WORKSPACE_HOST_ROOT"))
    container_root = _clean_path(
        os.environ.get("ODYSSEUS_WORKSPACE_CONTAINER_ROOT") or "/workspace"
    )
    if host_root and container_root:
        pairs.append((host_root, container_root))

    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def workspace_mount_pairs() -> list[tuple[str, str]]:
    """Configured (host_root, container_root) mount pairs, longest-first."""
    return _path_pairs()


def backend_workspace_path(path: str | None) -> str:
    """Translate a user-facing host workspace path into a backend-visible path."""
    original = str(path or "").strip()
    if not original or "\n" in original or "\r" in original:
        return ""

    expanded = os.path.abspath(os.path.expanduser(original))
    for host_root, container_root in _path_pairs():
        try:
            common = os.path.commonpath([host_root, expanded])
        except ValueError:
            continue
        if common != host_root:
            continue
        rel = os.path.relpath(expanded, host_root)
        if rel == ".":
            return container_root
        return str(PurePosixPath(container_root) / rel)
    return expanded
