"""Cross-process + in-process advisory lock for small JSON state files.

``memory.json`` is read-modify-written by many call sites: the main app, its
FastAPI threadpool, background scheduler tasks, and a *separate* memory MCP stdio
subprocess. ``MemoryManager.save()`` does an atomic ``os.replace``, which prevents
*torn* files but does NOT prevent lost updates — two ``load -> modify -> save``
cycles racing each other silently drop one write. ``file_lock()`` serializes the
whole cycle so the read and the write are one atomic unit.

Two layers:

* **in-process** — a per-path ``threading.Lock`` (module-global, keyed by the
  file's absolute path). Serializes threads within one process, including the
  FastAPI threadpool and any two ``MemoryManager`` instances pointed at the same
  file.
* **cross-process** — an advisory lock (``fcntl.flock`` on POSIX,
  ``msvcrt.locking`` on Windows) on a *sidecar* ``"<file>.lock"`` — never the data
  file itself, because ``os.replace`` swaps the inode and would silently drop a
  lock held on the data file's descriptor.

If neither ``fcntl`` nor ``msvcrt`` is importable (a restricted/sandboxed runtime),
the cross-process layer degrades to a no-op (warned once); the in-process layer
still protects the default single-worker uvicorn deployment.

Lock order is **thread-outer, file-inner**: only one thread per process ever
contends for the OS lock, which avoids an OS-level lock queue and extra fds.

NOT reentrant: ``file_lock()`` must wrap a complete cycle and the code inside it
must use the lock-free primitives (``load_all``/``save``), never another
``file_lock()`` on the same file.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - platform dependent
    msvcrt = None  # type: ignore[assignment]

_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: "dict[str, threading.Lock]" = {}
_warned_no_oslock = False


def _thread_lock_for(path: str) -> threading.Lock:
    """Return the process-wide threading.Lock for ``path`` (created on first use)."""
    key = os.path.abspath(path)
    with _LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


def _warn_no_oslock_once() -> None:
    global _warned_no_oslock
    if not _warned_no_oslock:
        _warned_no_oslock = True
        logger.warning(
            "Neither fcntl nor msvcrt is available; cross-process file locking is "
            "disabled. In-process locking still applies, so the default "
            "single-worker deployment is safe, but multi-process deployments "
            "(e.g. the memory MCP subprocess) are not serialized on this platform."
        )


@contextmanager
def file_lock(data_file: str) -> Iterator[None]:
    """Serialize a read-modify-write of ``data_file`` across threads and processes.

    Wrap the entire ``load -> modify -> save`` cycle. Not reentrant: do not call
    ``file_lock()`` (or anything that does) again from inside the ``with`` block.
    """
    tlock = _thread_lock_for(data_file)
    tlock.acquire()
    lock_path = data_file + ".lock"
    fh = None
    try:
        if fcntl is not None or msvcrt is not None:
            try:
                # Ensure the parent dir exists so opening the sidecar can't fail.
                parent = os.path.dirname(lock_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                fh = open(lock_path, "a+")
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                else:  # Windows
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            except OSError as exc:
                # Lock acquisition failed (e.g. filesystem without flock). Fall back
                # to in-process-only protection rather than crashing the write.
                logger.warning(
                    "file_lock: OS lock on %s failed (%s); proceeding with "
                    "in-process lock only", lock_path, exc,
                )
                if fh is not None:
                    fh.close()
                    fh = None
        else:
            _warn_no_oslock_once()
        yield
    finally:
        if fh is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                else:  # Windows
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            fh.close()
        tlock.release()
