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
from contextlib import contextmanager
from typing import Any, Optional


_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCK_DEPTH = threading.local()


def _reset_store_locks_after_fork():
    global _STORE_LOCKS, _STORE_LOCKS_GUARD, _STORE_LOCK_DEPTH
    _STORE_LOCKS = {}
    _STORE_LOCKS_GUARD = threading.Lock()
    _STORE_LOCK_DEPTH = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_store_locks_after_fork)


@contextmanager
def store_lock(store_path):
    """Hold one reentrant read/modify/write lock for this store path."""
    path = os.path.abspath(str(store_path)) + ".lock"
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.setdefault(path, threading.RLock())
    with lock:
        depths = getattr(_STORE_LOCK_DEPTH, "paths", None)
        if depths is None:
            depths = _STORE_LOCK_DEPTH.paths = {}
        if depths.get(path, 0):
            depths[path] += 1
            try:
                yield
            finally:
                depths[path] -= 1
            return
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
            depths[path] = 1
            try:
                yield
            finally:
                del depths[path]
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def store_transaction(path_factory):
    """Serialize a JSON read/modify/write across runtime threads and processes."""
    def decorate(function):
        @functools.wraps(function)
        def locked(*args, **kwargs):
            with store_lock(path_factory()):
                return function(*args, **kwargs)
        return locked
    return decorate


def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None, ensure_ascii: bool = True) -> None:
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
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
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
