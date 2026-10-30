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


def atomic_write_json(
    path: str,
    data: Any,
    *,
    indent: Optional[int] = None,
    file_mode: Optional[int] = None,
) -> None:
    """Atomically persist `data` as JSON at `path`.

    The temp file uses a random suffix so two concurrent writers saving the
    same file don't collide on the rename target. A PID suffix does not do
    this: the PID is constant for the life of a process, so two writers on
    the same path within one process (or one single-process container, where
    the PID never changes at all) still race for the same temp file.

    When `file_mode` is given (e.g. 0o600 for secret stores), the temp file
    is created with exactly those permission bits *before* any content is
    written, so a secret never lands in a temp file that the process umask
    would have opened up to other local accounts. `os.replace` preserves the
    temp file's mode, so the published file inherits it. When omitted, the
    temp file keeps the default ``0o666 & ~umask`` behavior all existing
    callers rely on.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{uuid.uuid4().hex}"

    try:
        with _open_tmp(tmp, file_mode) as f:
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


def _open_tmp(tmp: str, file_mode: Optional[int]):
    """Open the temp file, applying `file_mode` before any bytes are written.

    The mode is passed to `os.open` so the descriptor is born restricted
    (creation modes are masked by the umask, which can only remove bits), and
    an explicit `os.fchmod` on POSIX then forces the exact bits regardless of
    the umask. `O_EXCL` guarantees we never write through a pre-existing
    entry at the temp name. On Windows neither step changes anything
    meaningful — there are no POSIX permission bits — and the caller's
    `safe_chmod` remains the documented no-op.
    """
    if file_mode is None:
        return open(tmp, "w", encoding="utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, file_mode)
        return os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


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
