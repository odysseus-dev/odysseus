"""Block agent writes and shell mutations on a few sensitive paths.

This is on unless a config file turns it off. The default protected paths
are /etc, ~/.ssh and ~/.gnupg. A config file may add paths. It does not
replace the sandbox.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG = Path.home() / ".config" / "odysseus" / "state-gate.json"
DEFAULT_PROTECTED = ["/etc", "~/.ssh", "~/.gnupg"]
_COUNT = {"key": None, "n": 0}
_MUTATION = re.compile(
    r"(?:^|[;&|]\s*)(rm|mv|cp|tee|sed\s+-i|truncate|dd|install|chmod|chown)\b|>>?\s*\S|<<"
)


def config() -> Dict[str, Any]:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


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


def _count(cfg: Dict[str, Any], ctx: Optional[Dict[str, Any]]) -> Optional[str]:
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
        try:
            target.relative_to(base)
        except ValueError:
            continue
        return f"StateGate: {action} blocked on protected path {base} ({raw_path})"
    if action == "deploy" and not cfg.get("allow_deploy", False):
        return "StateGate: deploy blocked because allow_deploy is false"
    return _count(cfg, ctx)


def check_bash(command: str, *, ctx: Optional[Dict[str, Any]] = None) -> Optional[str]:
    cfg = config()
    if not _enabled(cfg):
        return None
    if not _MUTATION.search(command or ""):
        return None
    raw = str(command)
    for base in _protected(cfg):
        home = str(Path.home())
        tilde = "~" + str(base).replace(home, "", 1) if str(base).startswith(home) else ""
        if str(base) in raw or (tilde and tilde in raw):
            return f"StateGate: command blocked because it touches protected path {base}"
    return _count(cfg, ctx)
