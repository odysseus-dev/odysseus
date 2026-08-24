"""Shared project-level operating rules for coding agents and Mission Control.

This module intentionally has no FastAPI dependency so the same rules are used
by the HTTP UI, prompt construction, and the tool dispatcher.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


CONFIG_DIR = ".odysseus"
CONFIG_FILE = "project.json"
CHECKPOINT_REF = "refs/odysseus/checkpoints"


def project_defaults() -> dict[str, Any]:
    return {
        "instructions": "",
        "test_command": "",
        "protected_paths": [],
        "permission_rules": [],
        "completion_hooks": [],
        "checkpoint_before_changes": True,
        "visual_qa_url": "",
        "github_base_branch": "main",
        "context_compaction_percent": 80,
    }


def project_config_path(workspace: str) -> Path:
    root = Path(workspace).resolve()
    path = (root / CONFIG_DIR / CONFIG_FILE).resolve()
    if root not in path.parents:
        raise ValueError("Invalid project config path")
    return path


def load_project_config(workspace: str) -> dict[str, Any]:
    config = project_defaults()
    path = project_config_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            config.update({key: value for key, value in payload.items() if key in config})
    except (OSError, ValueError, TypeError):
        pass
    config["workspace"] = str(Path(workspace).resolve())
    config["config_path"] = str(path)
    return config


def save_project_config(workspace: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = project_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {key: payload.get(key, default) for key, default in project_defaults().items()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(serializable, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return load_project_config(workspace)


def protected_path_match(workspace: str, raw_path: str, patterns: list[str]) -> str | None:
    """Return the matching protected-path pattern, if any.

    Both direct paths (``.env``) and globs (``secrets/**``) are supported. The
    comparison is case-insensitive on Windows and is always made relative to
    the active workspace.
    """
    if not raw_path or not patterns:
        return None
    root = Path(workspace).resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.resolve(strict=False).relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    comparable = relative.casefold() if os.name == "nt" else relative
    basename = Path(relative).name.casefold() if os.name == "nt" else Path(relative).name
    for value in patterns:
        pattern = str(value or "").strip().replace("\\", "/").lstrip("./")
        if not pattern:
            continue
        folded = pattern.casefold() if os.name == "nt" else pattern
        if fnmatch.fnmatchcase(comparable, folded) or (
            "/" not in folded and fnmatch.fnmatchcase(basename, folded)
        ):
            return value
    return None


def tool_target_paths(tool: str, content: str) -> list[str]:
    """Extract deterministic file targets from native mutating file tools."""
    stripped = (content or "").strip()
    if tool in {"write_file", "edit_file"}:
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
                if isinstance(payload, dict) and payload.get("path"):
                    return [str(payload["path"]).strip()]
            except (ValueError, TypeError):
                return []
        return [(content or "").split("\n", 1)[0].strip()]
    if tool == "apply_patch":
        patch = content or ""
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
                if isinstance(payload, dict):
                    patch = str(payload.get("patch_text") or payload.get("patchText") or payload.get("patch") or "")
            except (ValueError, TypeError):
                return []
        return [
            match.strip()
            for match in re.findall(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", patch, re.MULTILINE)
            if match.strip()
        ]
    return []


def _run_git(workspace: str, *args: str, timeout: int = 8) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Git is not installed")
    kwargs: dict[str, Any] = {
        "cwd": workspace,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run([git, "-c", f"safe.directory={workspace}", *args], **kwargs)


def list_checkpoints(workspace: str, limit: int = 30) -> list[dict[str, Any]]:
    try:
        proc = _run_git(
            workspace,
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname)%00%(objectname)%00%(creatordate:iso-strict)",
            CHECKPOINT_REF,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) == 3:
            ref, sha, created_at = parts
            rows.append({"id": ref.rsplit("/", 1)[-1], "ref": ref, "sha": sha, "short_sha": sha[:8], "created_at": created_at})
    return rows[:limit]


def create_checkpoint(workspace: str, label: str) -> dict[str, Any]:
    inside = _run_git(workspace, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RuntimeError("Workspace is not a Git repository")
    status = _run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
    untracked = sum(1 for line in status.stdout.splitlines() if line.startswith("??")) if status.returncode == 0 else 0
    created = _run_git(workspace, "stash", "create", f"Odysseus checkpoint: {label or 'manual'}")
    sha = created.stdout.strip() if created.returncode == 0 else ""
    if not sha:
        head = _run_git(workspace, "rev-parse", "HEAD")
        sha = head.stdout.strip() if head.returncode == 0 else ""
    if not sha:
        raise RuntimeError("Could not snapshot this repository")
    safe_label = re.sub(r"[^a-zA-Z0-9._-]+", "-", (label or "checkpoint").strip()).strip("-._")[:48] or "checkpoint"
    checkpoint_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{safe_label}"
    ref = f"{CHECKPOINT_REF}/{checkpoint_id}"
    updated = _run_git(workspace, "update-ref", ref, sha)
    if updated.returncode != 0:
        raise RuntimeError((updated.stderr or "Could not save checkpoint").strip())
    return {
        "id": checkpoint_id,
        "ref": ref,
        "sha": sha,
        "short_sha": sha[:8],
        "tracked_only": True,
        "untracked_files": untracked,
    }
