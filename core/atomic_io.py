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
import uuid
import functools
import threading
from typing import Any, Optional


_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def store_transaction(path_factory):
    """Serialize a JSON read/modify/write across runtime threads and processes."""
    def decorate(function):
        @functools.wraps(function)
        def locked(*args, **kwargs):
            path = os.path.abspath(str(path_factory())) + ".lock"
            with _STORE_LOCKS_GUARD:
                lock = _STORE_LOCKS.setdefault(path, threading.RLock())
            with lock:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a+b") as handle:
                    if os.name == "nt":
                        import msvcrt
                        if os.fstat(handle.fileno()).st_size == 0:
                            handle.write(b"0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX)
                    try:
                        return function(*args, **kwargs)
                    finally:
                        if os.name == "nt":
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(handle, fcntl.LOCK_UN)
        return locked
    return decorate


def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None) -> None:
    """Atomically persist `data` as JSON at `path`.

    The temp file uses a random suffix so two concurrent writers saving the
    same file don't collide on the rename target. A PID suffix does not do
    this: the PID is constant for the life of a process, so two writers on
    the same path within one process (or one single-process container, where
    the PID never changes at all) still race for the same temp file.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{uuid.uuid4().hex}"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        # Directly unlink to avoid a check-then-act race condition.
        # Swallows FileNotFoundError (on success path) and other cleanup OSErrors.
        try:
            os.unlink(tmp)
        except OSError:
            pass


def atomic_write_text(path: str, text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("atomic_write_text expects a string")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{uuid.uuid4().hex}"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        # Directly unlink to avoid a check-then-act race condition.
        # Swallows FileNotFoundError (on success path) and other cleanup OSErrors.
        try:
            os.unlink(tmp)
        except OSError:
            pass
