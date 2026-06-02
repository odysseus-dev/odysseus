"""App log file enumeration and tailing for admin API and CLI."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.constants import LOGS_DIR

MAX_TAIL_LINES = 2000
DEFAULT_TAIL_LINES = 200

_SCRUB_PATTERNS = (
    (re.compile(r"(api[_-]?key\s*[=:]\s*)\S+", re.I), r"\1***"),
    (re.compile(r"(password\s*[=:]\s*)\S+", re.I), r"\1***"),
    (re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.I), r"\1***"),
    (re.compile(r"(Authorization:\s*)\S+", re.I), r"\1***"),
)

def _logs_root() -> Path:
    return Path(LOGS_DIR)


def _under_logs_dir(path: Path) -> bool:
    try:
        path.resolve().relative_to(_logs_root().resolve())
    except ValueError:
        return False
    return True


def enumerate_logs() -> list[dict[str, Any]]:
    """Return every app log under logs/ as {name, bytes, modified}."""
    root = _logs_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in root.glob("*.log"):
        if not p.is_file() or not _under_logs_dir(p):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({
            "name": p.name,
            "bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def resolve_log(name: str) -> Path | None:
    """Resolve a log filename under logs/; reject path traversal."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    filename = name if name.endswith(".log") else f"{name}.log"
    if filename != os.path.basename(filename):
        return None
    root = _logs_root()
    if not root.is_dir():
        return None
    for p in root.glob("*.log"):
        if not p.is_file() or not _under_logs_dir(p):
            continue
        if p.name == filename or (not name.endswith(".log") and p.stem == name):
            return p
    return None


def scrub_line(line: str) -> str:
    for pattern, repl in _SCRUB_PATTERNS:
        line = pattern.sub(repl, line)
    return line


def _read_tail_lines(path: Path, n: int) -> tuple[list[str], bool]:
    """Read last n lines from EOF without scanning the whole file."""
    block = 8192
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        if pos == 0:
            return [], False
        buf = b""
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
        lines = buf.decode("utf-8", errors="replace").splitlines()
        truncated = pos > 0 and len(lines) > n
        return lines[-n:], truncated


def tail_log(name: str, lines: int = DEFAULT_TAIL_LINES) -> dict[str, Any] | None:
    """Return last N lines of a log file with metadata."""
    path = resolve_log(name)
    if path is None:
        return None
    n = max(1, min(int(lines), MAX_TAIL_LINES))
    try:
        st = path.stat()
    except OSError:
        return None
    try:
        raw_lines, truncated = _read_tail_lines(path, n)
    except OSError:
        return None
    return {
        "name": path.name,
        "lines": [scrub_line(ln) for ln in raw_lines],
        "bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "truncated": truncated,
    }
