"""Gnexus governed-power operation guard for Juniperus.

This module is intentionally dependency-light and safe to import from high-power
execution paths. It classifies shell/file operations, allows low-risk read-only
inspection, and queues high-risk operations for human approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PACKAGE_ID = "JUNIPERUS040"
STATUS = "SHELL_FILE_GOVERNANCE_INTERCEPTOR_ACTIVE"
DEFAULT_WORKSPACE_ROOT = r"C:\Users\iamcy\CymaticsDev"

SENSITIVE_PATH_MARKERS = [
    ".env",
    "auth.json",
    "app.db",
    "juniperus.db",
    "compound.log",
    "api_token",
    "api-token",
    "token",
    "secret",
    "password",
    "credential",
    "private_key",
    "id_rsa",
    "id_ed25519",
    "\\.ssh\\",
    "/.ssh/",
    "\\AppData\\",
    "/AppData/",
    "\\data\\vault",
    "/data/vault",
]

MUTATION_PATTERNS = [
    r"\brm\b", r"\bdel\b", r"\berase\b", r"\brmdir\b", r"\brd\s+/s\b",
    r"\bremove-item\b", r"\bnew-item\b", r"\bset-content\b", r"\badd-content\b",
    r"\bout-file\b", r"\bmove-item\b", r"\bcopy-item\b", r"\brename-item\b",
    r"\bmkdir\b", r"\bmd\b", r"\btouch\b", r">", r">>", r"\|\s*tee\b",
    r"\bgit\s+(add|commit|push|pull|merge|rebase|reset|checkout|switch|clean|stash|tag|branch\s+-d)\b",
    r"\bnpm\s+(install|i|update|audit\s+fix|run\s+build|run\s+dev|start)\b",
    r"\bpnpm\s+(install|add|update|run\s+dev|run\s+build|start)\b",
    r"\byarn\s+(install|add|upgrade|start|dev|build)\b",
    r"\bpip\s+install\b", r"\bpython\s+-m\s+pip\s+install\b",
    r"\bdocker\s+(run|compose\s+up|build|rm|rmi|system\s+prune)\b",
    r"\bstart-process\b", r"\bstop-process\b", r"\btaskkill\b", r"\bsc\s+(start|stop|delete|create)\b",
    r"\bcurl\b", r"\binvoke-webrequest\b", r"\biwr\b", r"\bwget\b",
    r"\bpowershell\b.*\b-file\b", r"\bpwsh\b.*\b-file\b",
]

READ_ONLY_PREFIXES = [
    "dir", "ls", "pwd", "echo", "where", "which", "whoami", "hostname",
    "git status", "git diff", "git log", "git branch", "git remote", "git rev-parse",
    "type", "cat", "get-content", "gc", "select-string", "findstr",
    "get-childitem", "gci", "get-location", "gl", "test-path",
    "python --version", "python -v", "node --version", "npm --version", "git --version",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _workspace_root() -> Path:
    env = os.getenv("GNEXUS_WORKSPACE_ROOT") or os.getenv("JUNIPERUS_WORKSPACE_ROOT")
    if env:
        return Path(env)
    cfg = _load_json(_repo_root() / "config" / "gnexus.workspace.example.json", {})
    value = cfg.get("workspaceRoot") or cfg.get("canonicalWorkspaceRoot") or DEFAULT_WORKSPACE_ROOT
    return Path(value)


def _normalize_path(path_text: str) -> Path:
    raw = (path_text or "").strip().strip('"').strip("'")
    if not raw:
        return Path("")
    p = Path(raw)
    if not p.is_absolute():
        p = (_repo_root() / p)
    try:
        return p.resolve(strict=False)
    except Exception:
        return p.absolute()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except Exception:
        return False


def _is_sensitive_path(path_text: str) -> bool:
    low = (path_text or "").replace("/", "\\").lower()
    return any(marker.lower() in low for marker in SENSITIVE_PATH_MARKERS)


def _append_intercept(entry: Dict[str, Any]) -> None:
    path = _repo_root() / "data" / "gnexus" / "interceptor" / "intercept-ledger.json"
    ledger = _load_json(path, [])
    if not isinstance(ledger, list):
        ledger = []
    ledger.append(entry)
    _write_json(path, ledger[-500:])


def _append_approval(entry: Dict[str, Any]) -> None:
    path = _repo_root() / "data" / "gnexus" / "approval-queue.json"
    queue = _load_json(path, [])
    if isinstance(queue, dict):
        items = queue.get("items") or queue.get("approvals") or []
        if not isinstance(items, list):
            items = []
        items.append(entry)
        queue["items"] = items[-500:]
        queue["updatedAt"] = _now()
        _write_json(path, queue)
        return
    if not isinstance(queue, list):
        queue = []
    queue.append(entry)
    _write_json(path, queue[-500:])


def _approval_id(kind: str, payload: str) -> str:
    digest = hashlib.sha256((kind + "\n" + payload).encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"approval-{PACKAGE_ID.lower()}-{digest}-{uuid.uuid4().hex[:6]}"


def _queue_block(kind: str, source: str, reason: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    approval_id = _approval_id(kind, json.dumps(payload, sort_keys=True, default=str))
    entry = {
        "id": approval_id,
        "package": PACKAGE_ID,
        "status": "PENDING_HUMAN_APPROVAL",
        "kind": kind,
        "source": source,
        "reason": reason,
        "payload": payload,
        "createdAt": _now(),
        "executionUnlocked": False,
    }
    _append_approval(entry)
    intercept = {
        "package": PACKAGE_ID,
        "status": "BLOCKED_AND_QUEUED",
        "approvalId": approval_id,
        "kind": kind,
        "source": source,
        "reason": reason,
        "payloadHash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8", errors="replace")).hexdigest(),
        "createdAt": _now(),
    }
    _append_intercept(intercept)
    return {
        "blocked": True,
        "queued": True,
        "approvalId": approval_id,
        "status": intercept["status"],
        "reason": reason,
        "message": f"Blocked by Gnexus governance and queued for human approval: {approval_id}",
    }


def _allow(kind: str, source: str, reason: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    entry = {
        "package": PACKAGE_ID,
        "status": "ALLOWED_READ_ONLY",
        "kind": kind,
        "source": source,
        "reason": reason,
        "payloadHash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8", errors="replace")).hexdigest(),
        "createdAt": _now(),
    }
    _append_intercept(entry)
    return {"blocked": False, "queued": False, "status": "ALLOWED_READ_ONLY", "reason": reason}


def _looks_read_only_command(command: str) -> bool:
    compact = " ".join((command or "").strip().split()).lower()
    if not compact:
        return False
    for pattern in MUTATION_PATTERNS:
        if re.search(pattern, compact, flags=re.IGNORECASE):
            return False
    return any(compact == prefix or compact.startswith(prefix + " ") for prefix in READ_ONLY_PREFIXES)


def evaluate_shell_command(command: str, source: str = "shell") -> Dict[str, Any]:
    payload = {"command": command, "workspaceRoot": str(_workspace_root())}
    if not command or not command.strip():
        return _queue_block("shell", source, "empty command rejected", payload)
    if _looks_read_only_command(command):
        return _allow("shell", source, "read-only shell inspection", payload)
    return _queue_block("shell", source, "shell command is mutating, networked, launching, or not on the read-only allowlist", payload)


def evaluate_file_read(path_text: str, source: str = "read_file") -> Dict[str, Any]:
    workspace = _workspace_root()
    path = _normalize_path(path_text)
    payload = {"path": str(path), "workspaceRoot": str(workspace)}
    if not path_text or not str(path_text).strip():
        return _queue_block("read_file", source, "empty path rejected", payload)
    if _is_sensitive_path(str(path)):
        return _queue_block("read_file", source, "sensitive file read requires approval", payload)
    if not _inside(path, workspace):
        return _queue_block("read_file", source, "outside workspace read requires approval", payload)
    return _allow("read_file", source, "non-sensitive workspace read", payload)


def evaluate_file_write(path_text: str, content: str = "", source: str = "write_file") -> Dict[str, Any]:
    workspace = _workspace_root()
    path = _normalize_path(path_text)
    payload = {"path": str(path), "workspaceRoot": str(workspace), "contentLength": len(content or "")}
    if not path_text or not str(path_text).strip():
        return _queue_block("write_file", source, "empty path rejected", payload)
    if not _inside(path, workspace):
        return _queue_block("write_file", source, "outside workspace write requires approval", payload)
    return _queue_block("write_file", source, "file write requires human approval and diff-first gate", payload)


def evaluate_agent_tool(tool: str, content: str, source: str = "agent_tool_execution") -> Dict[str, Any]:
    tool = (tool or "").strip()
    if tool == "bash":
        return evaluate_shell_command(content, source=source)
    if tool == "read_file":
        path = (content or "").split("\n", 1)[0].strip()
        return evaluate_file_read(path, source=source)
    if tool == "write_file":
        lines = (content or "").split("\n", 1)
        path = lines[0].strip() if lines else ""
        body = lines[1] if len(lines) > 1 else ""
        return evaluate_file_write(path, body, source=source)
    return {"blocked": False, "queued": False, "status": "NOT_INTERCEPTED", "reason": "tool not governed by JUNIPERUS040"}


def get_interceptor_state() -> Dict[str, Any]:
    root = _repo_root()
    return {
        "package": PACKAGE_ID,
        "status": STATUS,
        "generatedAt": _now(),
        "repoRoot": str(root),
        "workspaceRoot": str(_workspace_root()),
        "interceptLedger": _load_json(root / "data" / "gnexus" / "interceptor" / "intercept-ledger.json", []),
        "approvalQueue": _load_json(root / "data" / "gnexus" / "approval-queue.json", []),
    }
