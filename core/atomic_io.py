"""Atomic JSON file writes.

Use this everywhere a JSON config file is persisted. A plain `open("w") +
json.dump` truncates the file on first write and only fills it with new
content afterwards — a kill -9 / power loss / OOM in between produces a
truncated or empty file. For password DBs (`auth.json`) and live state
(`sessions.json`, `settings.json`, `integrations.json`, `cookbook_state.json`),
that's a data-loss event.

`atomic_write_json` writes to a sibling tmp file, fsyncs, then `os.replace`s
into place. On POSIX `os.replace` is atomic on the same filesystem.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional


def _fsync_parent_dir(path: str) -> None:
    """Best-effort fsync of the containing directory after an atomic replace."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    try:
        fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: str, writer) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    parent = os.path.dirname(os.path.abspath(path)) or "."
    prefix = f".{os.path.basename(path)}.tmp."
    fd, tmp = tempfile.mkstemp(prefix=prefix, dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_parent_dir(path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None) -> None:
    """Atomically persist `data` as JSON at `path`.

    The temp file is created with ``mkstemp`` in the target directory, so
    concurrent writes in the same process or across processes cannot collide.
    """
    def write_json(f):
        json.dump(data, f, indent=indent)

    _atomic_write(path, write_json)


def atomic_write_text(path: str, text: str) -> None:
    def write_text(f):
        f.write(text)

    _atomic_write(path, write_text)
