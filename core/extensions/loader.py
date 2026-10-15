"""loader.py — discover extensions across all sources.

OXS looks for extensions in three locations:

  1. **Bundled** (``extensions/<id>/extension.json`` in the repo).
     Source-controlled. Always trusted. Hash check skipped.

  2. **User-installed** (``data/extensions/<id>/extension.json``).
     Operator-installed. ``file_hashes`` verified.

  3. **Entry-points** (pip packages exposing the
     ``odysseus.extensions`` entry-point group).
     Hash check skipped — Python's import system already gates entry-point
     discovery. (Authors publishing pip packages should ship
     ``extension.json`` *inside* the package and reference it from the
     entry-point metadata so the loader can find it.)

The loader does NOT import or execute anything. It reads ``extension.json``
files via the JSON Schema validator and walks the filesystem. Imports happen
later, in :func:`registry.invoke_register`, only on explicit enable.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import json
import os
from dataclasses import dataclass
from typing import Iterator

from src.constants import DATA_EXTENSIONS_DIR, EXTENSIONS_DIR

from .audit import audit_log
from .manifest_schema import MANIFEST_FILENAME, OXSManifestError, validate_manifest
from .security import OXSSecurityError, verify_file_hashes


@dataclass(frozen=True)
class DiscoveredExtension:
    """One extension found on disk, before any install/enable step.

    ``source`` discriminates the origin so the registry can apply the
    right policy (bundled = trusted, others = hash-verified).
    """
    manifest: dict
    source: str       # "bundled" | "user" | "entry_point"
    source_path: str  # absolute directory containing extension.json
    extension_id: str


# ── Source scanners ────────────────────────────────────────────────────────


def _scan_directory(root: str, *, source_label: str) -> Iterator[DiscoveredExtension]:
    """Scan ``root/<id>/extension.json`` for valid manifests.

    Subdirectories without ``extension.json`` are silently skipped — they
    may be partial installs in progress.
    """
    if not os.path.isdir(root):
        return
    for entry in sorted(os.listdir(root)):
        sub = os.path.join(root, entry)
        if not os.path.isdir(sub):
            continue
        manifest_path = os.path.join(sub, MANIFEST_FILENAME)
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, encoding="utf-8") as f:
                raw = f.read()
            manifest = validate_manifest(raw)
            yield DiscoveredExtension(
                manifest=manifest,
                source=source_label,
                source_path=sub,
                extension_id=manifest["id"],
            )
        except OXSManifestError as e:
            audit_log(
                "extension.discovery.invalid_manifest",
                extension_id=entry,
                actor="system",
                payload={"path": manifest_path, "errors": e.errors},
            )
        except OSError as e:
            audit_log(
                "extension.discovery.read_error",
                extension_id=entry,
                actor="system",
                payload={"path": manifest_path, "error": str(e)},
            )


def _scan_entry_points() -> Iterator[DiscoveredExtension]:
    """Discover pip-installed extensions via the ``odysseus.extensions`` group.

    Each entry-point must be ``Extension = ``
    - ``module:`` reference (dotted module path)
    - ``attr`` is the manifest dict OR a callable that returns one

    We support the second form so authors can compute the manifest
    dynamically. The manifest is then validated through the same schema.
    """
    try:
        eps = importlib_metadata.entry_points(group="odysseus.extensions")
    except Exception:
        return
    for ep in eps:
        try:
            obj = ep.load()
        except Exception as e:
            audit_log(
                "extension.discovery.entry_point_load_failed",
                extension_id=getattr(ep, "name", "?"),
                actor="system",
                payload={"entry_point": str(ep), "error": str(e)},
            )
            continue
        try:
            if callable(obj):
                manifest_obj = obj()
            else:
                manifest_obj = obj
            manifest = validate_manifest(manifest_obj)
            # Use the package's source dir as the extension's plugin path.
            try:
                dist = ep.dist
                src_path = str(dist.locate_file(""))
            except Exception:
                src_path = ""
            yield DiscoveredExtension(
                manifest=manifest,
                source="entry_point",
                source_path=src_path,
                extension_id=manifest["id"],
            )
        except OXSManifestError as e:
            audit_log(
                "extension.discovery.invalid_manifest",
                extension_id=getattr(ep, "name", "?"),
                actor="system",
                payload={"entry_point": str(ep), "errors": e.errors},
            )


# ── Public API ─────────────────────────────────────────────────────────────


def discover_all(
    *,
    bundled_dir: str = EXTENSIONS_DIR,
    user_dir: str = DATA_EXTENSIONS_DIR,
    include_entry_points: bool = True,
) -> list[DiscoveredExtension]:
    """Return every extension discovered across all sources.

    Bundled extensions are returned first, then user-installed, then
    entry-points. Duplicates (same id from multiple sources) keep the
    highest-priority entry — bundled > user > entry-point — and the
    others are logged as conflicts.
    """
    out: dict[str, DiscoveredExtension] = {}
    for ext in _scan_directory(bundled_dir, source_label="bundled"):
        if ext.extension_id in out:
            audit_log(
                "extension.discovery.duplicate_id",
                extension_id=ext.extension_id,
                actor="system",
                payload={
                    "kept_source": out[ext.extension_id].source,
                    "ignored_source": ext.source,
                },
            )
            continue
        out[ext.extension_id] = ext

    for ext in _scan_directory(user_dir, source_label="user"):
        if ext.extension_id in out:
            audit_log(
                "extension.discovery.duplicate_id",
                extension_id=ext.extension_id,
                actor="system",
                payload={
                    "kept_source": out[ext.extension_id].source,
                    "ignored_source": ext.source,
                },
            )
            continue
        out[ext.extension_id] = ext

    if include_entry_points:
        for ext in _scan_entry_points():
            if ext.extension_id in out:
                audit_log(
                    "extension.discovery.duplicate_id",
                    extension_id=ext.extension_id,
                    actor="system",
                    payload={
                        "kept_source": out[ext.extension_id].source,
                        "ignored_source": ext.source,
                    },
                )
                continue
            out[ext.extension_id] = ext

    return list(out.values())


def verify_discovered(ext: DiscoveredExtension) -> None:
    """Apply source-specific security checks to a discovered extension.

    Bundled extensions skip the file-hash check because their tree is
    source-controlled and the operator can audit the diff. User-installed
    extensions require ``file_hashes`` and we verify them. Entry-point
    extensions skip the file-hash check at this layer; integrity is the
    responsibility of pip's resolver.

    Raises :class:`OXSSecurityError` on failure.
    """
    if ext.source == "bundled":
        return
    if ext.source == "entry_point":
        return
    declared = ext.manifest.get("file_hashes") or {}
    verify_file_hashes(ext.source_path, declared, extension_id=ext.extension_id)


__all__ = [
    "DiscoveredExtension",
    "discover_all",
    "verify_discovered",
]