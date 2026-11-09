"""In-flight activity counters (heavy image ops) for safe restarts/observability.

A deploy restart kills any in-flight long image op (a diffusion edit is 60-120 s),
so the operator needs to know whether the process is busy before issuing
``systemctl --user restart``. These counters are process-local and cheap.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Dict

_lock = threading.Lock()
_image_ops = 0
_image_ops_by_name: Dict[str, int] = {}
_started_at = time.time()


def begin_image_op(name: str = "image") -> None:
    global _image_ops
    with _lock:
        _image_ops += 1
        _image_ops_by_name[name] = _image_ops_by_name.get(name, 0) + 1


def end_image_op(name: str = "image") -> None:
    global _image_ops
    with _lock:
        _image_ops = max(0, _image_ops - 1)
        if name in _image_ops_by_name:
            _image_ops_by_name[name] -= 1
            if _image_ops_by_name[name] <= 0:
                _image_ops_by_name.pop(name, None)


@contextmanager
def image_op(name: str = "image"):
    begin_image_op(name)
    try:
        yield
    finally:
        end_image_op(name)


def activity() -> Dict[str, object]:
    with _lock:
        return {
            "image_ops": _image_ops,
            "image_ops_by_name": dict(_image_ops_by_name),
            "uptime_s": round(time.time() - _started_at, 1),
        }


def heavy_image_ops_active() -> int:
    with _lock:
        return _image_ops


def busy() -> bool:
    return heavy_image_ops_active() > 0
