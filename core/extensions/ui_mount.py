"""ui_mount.py — mount extension static file directories under FastAPI.

Each extension can call ``host.add_static(url_prefix, directory)`` where
``directory`` is relative to the extension's plugin path (the directory
containing ``extension.json``). The directory is served at ``url_prefix``
by FastAPI's ``StaticFiles``.

This module keeps a registry of (extension_id, url_prefix, mounted_path)
triples so disable() can find and unmount the routes. Mounts are
attached to the FastAPI app passed in by the caller (see ``mount_all``)
or to a global singleton in tests.

Path safety:
  - ``url_prefix`` is normalized to begin with ``/`` and not end with ``/``.
  - ``directory`` is resolved against the extension's plugin path and
    rejected if it escapes via ``..``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MountedStatic:
    extension_id: str
    url_prefix: str
    source_directory: str


_state: list[MountedStatic] = []


def list_mounted() -> list[MountedStatic]:
    return list(_state)


def reset_for_tests() -> None:
    """Clear the mount registry. Tests only."""
    _state.clear()


def _normalize_prefix(prefix: str) -> str:
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if prefix.endswith("/") and len(prefix) > 1:
        prefix = prefix.rstrip("/")
    return prefix


def _resolve_directory(plugin_path: str, directory: str) -> str:
    """Resolve ``directory`` against ``plugin_path`` and ensure it stays inside.

    Rejects ``..`` traversal. The result must be a real directory.
    """
    if not directory:
        raise ValueError("add_static: directory is required")
    if os.path.isabs(directory):
        raise ValueError(
            f"add_static: directory must be relative to plugin_path (got {directory!r})"
        )
    candidate = os.path.abspath(os.path.join(plugin_path, directory))
    plugin_abs = os.path.abspath(plugin_path)
    # Use os.path.commonpath to defend against prefix confusion
    try:
        common = os.path.commonpath([plugin_abs, candidate])
    except ValueError:
        raise ValueError(
            f"add_static: directory {directory!r} resolves outside plugin_path"
        )
    if common != plugin_abs:
        raise ValueError(
            f"add_static: directory {directory!r} escapes plugin_path"
        )
    if not os.path.isdir(candidate):
        raise FileNotFoundError(
            f"add_static: directory does not exist: {candidate}"
        )
    return candidate


def install(
    app: Any,
    extension_id: str,
    url_prefix: str,
    plugin_path: str,
    directory: str,
) -> MountedStatic:
    """Mount ``directory`` (relative to ``plugin_path``) at ``url_prefix``.

    Returns the registration. The mount is reversible via :func:`uninstall`.
    """
    prefix = _normalize_prefix(url_prefix)
    source = _resolve_directory(plugin_path, directory)

    # Remove any existing mount at this prefix before re-adding.
    uninstall(app, extension_id, url_prefix=prefix)

    try:
        from fastapi.staticfiles import StaticFiles
    except ImportError as e:
        raise RuntimeError("FastAPI is required for ui_mount.install") from e

    app.mount(prefix, StaticFiles(directory=source, check_dir=True), name=f"oxs_{extension_id}_{prefix}")
    rec = MountedStatic(
        extension_id=extension_id,
        url_prefix=prefix,
        source_directory=source,
    )
    _state.append(rec)
    logger.info(
        "OXS mounted %s at %s for extension %s",
        source, prefix, extension_id,
    )
    return rec


def uninstall(
    app: Any,
    extension_id: str,
    *,
    url_prefix: str | None = None,
) -> int:
    """Unmount one or all mounts for ``extension_id``.

    If ``url_prefix`` is provided, only that prefix is removed; otherwise
    every mount belonging to the extension is removed. Returns the count.
    """
    prefix_norm = _normalize_prefix(url_prefix) if url_prefix else None
    kept: list[MountedStatic] = []
    removed = 0
    # Need a list of routes to unmount. FastAPI routes don't have a clean
    # "unmount" API on the app; the only reliable approach is to filter
    # the router's routes list. We use the internal Starlette router API.
    try:
        router = app.router
    except AttributeError:
        router = None

    for rec in _state:
        if rec.extension_id != extension_id:
            kept.append(rec)
            continue
        if prefix_norm is not None and rec.url_prefix != prefix_norm:
            kept.append(rec)
            continue
        if router is not None:
            new_routes = [r for r in router.routes if getattr(r, "path", "") != rec.url_prefix]
            # Starlette's routes are tuples (mount, sub_app) — filter all.
            router.routes = new_routes
        removed += 1
    _state.clear()
    _state.extend(kept)
    return removed


def mount_all(app: Any, registrations: list) -> int:
    """Install every ``StaticRegistration`` from a list.

    Each registration carries ``url_prefix`` and ``directory``. The
    caller is expected to know the plugin path (passed separately via
    the ``plugin_paths`` dict keyed by extension_id).
    """
    raise NotImplementedError(
        "mount_all needs an extension_id per registration; "
        "use ExtensionRegistry.mount_statics instead"
    )


__all__ = [
    "MountedStatic",
    "list_mounted",
    "install",
    "uninstall",
    "reset_for_tests",
]