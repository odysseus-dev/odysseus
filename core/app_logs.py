"""App log file enumeration and tailing for admin API and CLI."""

from __future__ import annotations

import os
import re
from collections import deque
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
    (re.compile(r"(Bearer\s+)\S+", re.I), r"\1***"),
)


def _logs_path() -> Path:
    return Path(LOGS_DIR)


def _under_logs_dir(path: Path) -> bool:
    try:
        path.resolve().relative_to(_logs_path().resolve())
    except ValueError:
        return False
    return True


def enumerate_logs() -> list[dict[str, Any]]:
    """Return every app log under logs/ as {name, bytes, modified}."""
    base = _logs_path()
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(base.glob("*.log")):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if not _under_logs_dir(p):
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
    base = name if name.endswith(".log") else f"{name}.log"
    if base != os.path.basename(base):
        return None
    candidates: list[Path] = []
    logs = _logs_path()
    if not logs.is_dir():
        return None
    for p in logs.glob("*.log"):
        if not p.is_file() or not _under_logs_dir(p):
            continue
        if p.name == base or p.stem == name or name in p.name:
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return candidates[0]


def scrub_line(line: str) -> str:
    for pattern, repl in _SCRUB_PATTERNS:
        line = pattern.sub(repl, line)
    return line


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
    raw_lines: list[str] = []
    truncated = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            dq: deque[str] = deque(maxlen=n)
            for line in f:
                dq.append(line.rstrip("\n\r"))
            raw_lines = list(dq)
            if st.st_size > 0 and len(raw_lines) == n:
                # Heuristic: file may have more lines than we kept
                f.seek(0)
                total = sum(1 for _ in f)
                truncated = total > n
    except OSError:
        return None
    return {
        "name": path.name,
        "lines": [scrub_line(ln) for ln in raw_lines],
        "bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "truncated": truncated,
    }
