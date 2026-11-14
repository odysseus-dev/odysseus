"""Block agent writes and shell mutations on a few sensitive paths.

This is on unless a config file turns it off. The default protected paths are
``/etc``, ``~/.ssh`` and ``~/.gnupg``; a config file may add paths, set
``enabled`` to false, or relax ``allow_deploy``. It is a guard before the
mutation, not a sandbox.

Two properties matter for the guard to be useful on every tool call:

* The config is cached by ``(inode, size, mtime_ns)``. It used to be read and
  parsed on every call; now it is re-read only when the file changes.
* Shell paths are resolved (``~``, ``$HOME``, relative paths and ``..``) and
  compared by path boundary, not by substring: ``~/.ssh-public`` no longer
  counts as ``~/.ssh`` and ``x/../.ssh`` does. Prefixes such as ``sudo``,
  ``env`` and ``xargs`` are unwrapped before looking for the mutation verb.

The guard still errs on the side of blocking when a command is opaque (for
example inline interpreter code). It is not a replacement for the sandbox.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG = Path.home() / ".config" / "odysseus" / "state-gate.json"
DEFAULT_PROTECTED = ["/etc", "~/.ssh", "~/.gnupg"]
_COUNT: Dict[str, Any] = {"key": None, "n": 0}
_CACHE: Optional[Dict[str, Any]] = None

_MUTATION = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(rm|mv|cp|tee|truncate|dd|install|chmod|chown|ln|unlink|shred|touch|mkdir|rmdir)\b"
    r"|\bsed\b[^;&|]*\s-\w*i\b"
    r"|-delete\b"
    r"|>>?\s*\S"
    r"|<<"
)
_PREFIX = re.compile(
    r"^\s*(?:(?:sudo|doas|command|nohup|time|builtin|exec)\b(?:\s+-\S+)*\s*"
    r"|env\b(?:\s+[A-Za-z_][A-Za-z0-9_]*=\S*)*\s*"
    r"|nice\b(?:\s+-n\s+\d+)?\s*)"
)
_INLINE_CODE = {"python", "python3", "perl", "ruby", "node", "bash", "sh", "zsh", "dash"}


def _config_stamp(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def config() -> Dict[str, Any]:
    """Current config, re-read only when the file changed (inode/size/mtime)."""
    global _CACHE
    path = Path(CONFIG)
    stamp = _config_stamp(path)
    if _CACHE is not None and _CACHE["path"] == path and _CACHE["stamp"] == stamp:
        return _CACHE["cfg"]
    cfg: Dict[str, Any] = {}
    if stamp is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = data
        except (OSError, ValueError):
            cfg = {}
    _CACHE = {"path": path, "stamp": stamp, "cfg": cfg}
    return cfg


def reset_config_cache() -> None:
    global _CACHE
    _CACHE = None


def _norm(raw: str) -> Path:
    return Path(os.path.expanduser(str(raw))).resolve()


def _enabled(cfg: Dict[str, Any]) -> bool:
    if "enabled" not in cfg:
        return True
    return bool(cfg.get("enabled"))


def _protected(cfg: Dict[str, Any]) -> List[Path]:
    paths = list(DEFAULT_PROTECTED)
    extra = cfg.get("protected")
    if isinstance(extra, list):
        for item in extra:
            text = str(item).strip()
            if text and text not in paths:
                paths.append(text)
    return [_norm(item) for item in paths]


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _resolve_token(token: str) -> Optional[Path]:
    """Resolve a token to an absolute path; None if it is not a usable path."""
    raw = token.strip()
    if not raw or raw.startswith("-") or "://" in raw:
        return None
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if not expanded or "\x00" in expanded:
        return None
    try:
        path = Path(expanded)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve()
    except (OSError, ValueError):
        return None


def _tokens(command: str) -> List[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _unwrap(command: str) -> str:
    text = command or ""
    for _ in range(3):
        new = _PREFIX.sub("", text, count=1)
        if new == text:
            break
        text = new
    # `xargs [-flags] rm ...` runs rm with the arguments it receives.
    return re.sub(r"\bxargs\b(?:\s+-\S+)*\s*", "", text)


def _is_mutating(command: str) -> bool:
    text = _unwrap(command)
    if _MUTATION.search(text):
        return True
    tokens = _tokens(text)
    if not tokens:
        return False
    head = os.path.basename(tokens[0])
    if head in _INLINE_CODE:
        # Inline code is opaque: it cannot be proven read-only.
        return any(t in {"-c", "-e", "-E", "--eval", "--execute"} for t in tokens[1:])
    return False


def _mentions(command: str, base: Path) -> bool:
    """Textual occurrence of the base with a path boundary (covers quoted code)."""
    needle = str(base)
    start = 0
    while True:
        idx = command.find(needle, start)
        if idx < 0:
            return False
        end = idx + len(needle)
        nxt = command[end:end + 1]
        if nxt in ("", "/", " ", "\t", "'", '"', "\\", ";", "|", "&"):
            return True
        start = end


def _count(cfg: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Optional[str]:
    limit = int(cfg.get("max_mutations_per_turn") or 0)
    if not limit:
        return None
    key = str((ctx or {}).get("request_id") or (ctx or {}).get("session_id") or "global")
    if _COUNT["key"] != key:
        _COUNT["key"], _COUNT["n"] = key, 0
    _COUNT["n"] += 1
    if _COUNT["n"] > limit:
        return f"StateGate: mutation limit of {limit} per turn exceeded"
    return None


def check_path(raw_path: str, *, action: str = "write", ctx: Optional[Dict[str, Any]] = None) -> Optional[str]:
    cfg = config()
    if not _enabled(cfg):
        return None
    target = _norm(raw_path)
    for base in _protected(cfg):
        if _under(target, base):
            return f"StateGate: {action} blocked on protected path {base} ({raw_path})"
    if action == "deploy" and not cfg.get("allow_deploy", False):
        return "StateGate: deploy blocked because allow_deploy is false"
    return _count(cfg, ctx)


def check_bash(command: str, *, ctx: Optional[Dict[str, Any]] = None) -> Optional[str]:
    cfg = config()
    if not _enabled(cfg):
        return None
    if not _is_mutating(command or ""):
        return None
    bases = _protected(cfg)
    for token in _tokens(command):
        resolved = _resolve_token(token)
        if resolved is None:
            continue
        for base in bases:
            if _under(resolved, base):
                return f"StateGate: command blocked because it touches protected path {base}"
    for base in bases:
        if _mentions(command, base):
            return f"StateGate: command blocked because it touches protected path {base}"
    return _count(cfg, ctx)


def reset_turn() -> None:
    _COUNT["n"] = 0
    reset_config_cache()
