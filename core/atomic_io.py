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
import stat
from typing import Any, Optional


def _fsync_parent_directory(path: str) -> None:
    """Best-effort durability for a completed same-directory rename."""
    if os.name != "posix":
        return

    directory = os.path.dirname(os.path.abspath(path)) or "."
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY

    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: str, writer, *, mode: Optional[int] = None) -> None:
    import tempfile

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        pass

    effective_mode = mode if mode is not None else existing_mode
    descriptor, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )

    try:
        if effective_mode is not None:
            os.fchmod(descriptor, effective_mode)

        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, path)
        _fsync_parent_directory(path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None) -> None:
    def write(stream) -> None:
        json.dump(data, stream, ensure_ascii=False, indent=indent)

    _atomic_write(path, write)


def atomic_write_text(path: str, text: str, *, mode: Optional[int] = None) -> None:
    if not isinstance(text, str):
        raise TypeError("atomic_write_text expects a string")

    def write(stream) -> None:
        stream.write(text)

    _atomic_write(path, write, mode=mode)
