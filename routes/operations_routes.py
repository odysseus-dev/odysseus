"""Mission Control APIs for local coding-agent operations.

The routes in this module deliberately keep inspection read-only by default.
State-changing operations (project config, checkpoints, worktrees, restore) are
explicit POST/PUT actions and remain confined to a vetted workspace.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.database import ScheduledTask, SessionLocal, TaskRun
from routes.workspace_routes import _default_workspace, _git_workspace_status, _run_git
from src.auth_helpers import get_current_user
from src.project_operations import (
    CHECKPOINT_REF as _CHECKPOINT_REF,
    create_checkpoint as _shared_create_checkpoint,
    list_checkpoints as _shared_list_checkpoints,
    load_project_config as _shared_load_project_config,
    project_config_path as _shared_project_config_path,
    project_defaults as _shared_project_defaults,
    save_project_config as _shared_save_project_config,
)
from src.tool_security import owner_is_admin_or_single_user


_MAX_DIFF_CHARS = 40_000


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_admin(request: Request) -> Optional[str]:
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        raise HTTPException(status_code=403, detail="Mission Control is admin-only")
    return owner


def _resolve_workspace(raw_path: str | None) -> str:
    from src.tool_execution import vet_workspace

    candidate = (raw_path or "").strip() or _default_workspace() or ""
    resolved = vet_workspace(candidate)
    if not resolved:
        raise HTTPException(status_code=400, detail="Invalid workspace")
    return resolved


def _project_config_path(workspace: str) -> Path:
    try:
        return _shared_project_config_path(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid project config path")


def _project_defaults() -> dict[str, Any]:
    return _shared_project_defaults()


def _load_project_config(workspace: str) -> dict[str, Any]:
    return _shared_load_project_config(workspace)


def _atomic_save_project_config(workspace: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _shared_save_project_config(workspace, payload)


def _safe_command(command: list[str], cwd: str, timeout: int = 6) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


def _probe_json(url: str, timeout: float = 1.5) -> tuple[bool, dict[str, Any], str, float]:
    started = time.perf_counter()
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        elapsed = round((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        data = response.json()
        return True, data if isinstance(data, dict) else {}, "", elapsed
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        return False, {}, str(exc), elapsed


def _ollama_base() -> str:
    value = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value.replace("0.0.0.0", "127.0.0.1").replace("[::]", "127.0.0.1")


def _gpu_snapshot() -> list[dict[str, Any]]:
    nvidia = shutil.which("nvidia-smi")
    if not nvidia:
        return []
    try:
        proc = _safe_command([
            nvidia,
            "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ], os.getcwd(), timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    rows = []
    for index, line in enumerate(proc.stdout.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            rows.append({
                "index": index,
                "name": parts[0],
                "memory_used_mb": int(parts[1]),
                "memory_total_mb": int(parts[2]),
                "utilization_percent": int(parts[3]),
                "temperature_c": int(parts[4]),
            })
        except ValueError:
            continue
    return rows


def _runtime_snapshot(workspace: str) -> dict[str, Any]:
    ollama = _ollama_base()
    ollama_ok, version_data, ollama_error, ollama_latency = _probe_json(f"{ollama}/api/version")
    loaded_models: list[dict[str, Any]] = []
    if ollama_ok:
        ps_ok, ps_data, _, _ = _probe_json(f"{ollama}/api/ps")
        if ps_ok:
            for model in ps_data.get("models", []) or []:
                if not isinstance(model, dict):
                    continue
                details = model.get("details") if isinstance(model.get("details"), dict) else {}
                loaded_models.append({
                    "name": model.get("name") or model.get("model") or "Unknown model",
                    "size_bytes": int(model.get("size") or 0),
                    "size_vram_bytes": int(model.get("size_vram") or 0),
                    "context_length": int(model.get("context_length") or 0),
                    "quantization": details.get("quantization_level") or "",
                    "expires_at": model.get("expires_at"),
                })

    chroma_host = (os.environ.get("CHROMADB_HOST") or "127.0.0.1").strip()
    chroma_port = (os.environ.get("CHROMADB_PORT") or "8100").strip()
    chroma_url = f"http://{chroma_host}:{chroma_port}"
    chroma_ok, _, chroma_error, chroma_latency = _probe_json(f"{chroma_url}/api/v2/heartbeat")

    searx_url = (os.environ.get("SEARXNG_URL") or "http://127.0.0.1:8080").strip().rstrip("/")
    search_ok, _, search_error, search_latency = _probe_json(f"{searx_url}/config")

    shell_path = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("bash") or shutil.which("sh")
    git_path = shutil.which("git")
    disk = shutil.disk_usage(workspace)
    return {
        "checked_at": _utc_iso(),
        "services": [
            {"id": "odysseus", "name": "Odysseus", "status": "healthy", "detail": "Mission Control API online", "latency_ms": 0},
            {"id": "ollama", "name": "Ollama", "status": "healthy" if ollama_ok else "offline", "detail": f"v{version_data.get('version', '')}" if ollama_ok else ollama_error, "latency_ms": ollama_latency, "url": ollama},
            {"id": "chroma", "name": "ChromaDB", "status": "healthy" if chroma_ok else "degraded", "detail": "Vector memory ready" if chroma_ok else "Optional vector memory unavailable", "error": chroma_error, "latency_ms": chroma_latency, "url": chroma_url},
            {"id": "search", "name": "Web search", "status": "healthy" if search_ok else "degraded", "detail": "SearXNG ready" if search_ok else "SearXNG unavailable; configured fallbacks may still work", "error": search_error, "latency_ms": search_latency, "url": searx_url},
            {"id": "shell", "name": "Shell", "status": "healthy" if shell_path else "offline", "detail": shell_path or "No supported shell found"},
            {"id": "git", "name": "Git", "status": "healthy" if git_path else "offline", "detail": git_path or "Git executable not found"},
        ],
        "ollama": {"version": version_data.get("version") if ollama_ok else None, "loaded_models": loaded_models},
        "gpus": _gpu_snapshot(),
        "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
    }


def _git_log_snapshot(workspace: str) -> dict[str, Any]:
    git = shutil.which("git")
    if not git:
        return {}
    result: dict[str, Any] = {}
    try:
        head = _run_git(git, workspace, "log", "-1", "--format=%H%x00%h%x00%s%x00%aI")
        if head.returncode == 0:
            parts = head.stdout.strip().split("\x00", 3)
            if len(parts) == 4:
                result["head"] = {"sha": parts[0], "short_sha": parts[1], "subject": parts[2], "authored_at": parts[3]}
        remote = _run_git(git, workspace, "remote", "get-url", "origin")
        result["origin"] = remote.stdout.strip() if remote.returncode == 0 else ""
        stats = []
        for args, staged in ((["diff", "--numstat"], False), (["diff", "--cached", "--numstat"], True)):
            proc = _run_git(git, workspace, *args)
            if proc.returncode != 0:
                continue
            for line in proc.stdout.splitlines():
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                stats.append({
                    "path": parts[2],
                    "additions": int(parts[0]) if parts[0].isdigit() else 0,
                    "deletions": int(parts[1]) if parts[1].isdigit() else 0,
                    "staged": staged,
                })
        result["file_stats"] = stats
        diff = _run_git(git, workspace, "diff", "--no-ext-diff", "--unified=2")
        result["diff_preview"] = (diff.stdout or "")[:_MAX_DIFF_CHARS] if diff.returncode == 0 else ""
        result["diff_truncated"] = bool(diff.returncode == 0 and len(diff.stdout or "") > _MAX_DIFF_CHARS)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def _checkpoint_slug(label: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (label or "checkpoint").strip()).strip("-._")
    return (cleaned or "checkpoint")[:48]


def _list_checkpoints(workspace: str) -> list[dict[str, Any]]:
    return _shared_list_checkpoints(workspace)


def _create_checkpoint(workspace: str, label: str) -> dict[str, Any]:
    try:
        return _shared_create_checkpoint(workspace, label)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Checkpoint command timed out")


def _worktrees(workspace: str) -> list[dict[str, Any]]:
    git = shutil.which("git")
    if not git:
        return []
    try:
        proc = _run_git(git, workspace, "worktree", "list", "--porcelain")
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*proc.stdout.splitlines(), ""]:
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["sha"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in {"detached", "locked", "prunable"}:
            current[key] = value or True
    return rows


class ProjectConfigUpdate(BaseModel):
    instructions: str = Field(default="", max_length=40_000)
    test_command: str = Field(default="", max_length=2_000)
    protected_paths: list[str] = Field(default_factory=list, max_length=200)
    permission_rules: list[str] = Field(default_factory=list, max_length=200)
    checkpoint_before_changes: bool = True
    visual_qa_url: str = Field(default="", max_length=2_000)
    github_base_branch: str = Field(default="main", max_length=200)
    context_compaction_percent: int = Field(default=80, ge=50, le=95)


class CheckpointCreate(BaseModel):
    label: str = Field(default="Manual", max_length=120)


class CheckpointRestore(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=100)
    confirmation: str = Field(default="", max_length=20)


class WorktreeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    base: str = Field(default="HEAD", max_length=200)


def setup_operations_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/operations", tags=["operations"])

    @router.get("/runtime")
    def runtime_status(request: Request, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        return {"workspace": workspace, **_runtime_snapshot(workspace)}

    @router.get("/context")
    def context_status(request: Request, session_id: str = Query(default="")):
        owner = _require_admin(request)
        if not session_id:
            return {"session_id": None, "used_tokens": 0, "context_length": 0, "context_percent": 0, "breakdown": {}}
        try:
            session = session_manager.get_session(session_id)
        except Exception:
            raise HTTPException(status_code=404, detail="Session not found")
        if owner and session.owner and session.owner != owner:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = session.get_context_messages()
        from src.model_context import estimate_tokens, get_context_length

        breakdown: dict[str, int] = {}
        for message in messages:
            role = str(message.get("role") or "other")
            breakdown[role] = breakdown.get(role, 0) + estimate_tokens([message])
        used = estimate_tokens(messages)
        context_length = get_context_length(session.endpoint_url, session.model) if session.endpoint_url and session.model else 0
        return {
            "session_id": session.id,
            "model": session.model,
            "used_tokens": used,
            "context_length": context_length,
            "context_percent": round((used / context_length) * 100, 1) if context_length else 0,
            "message_count": len(messages),
            "breakdown": breakdown,
        }

    @router.get("/review")
    def completion_review(
        request: Request,
        path: str = Query(default=""),
        session_id: str = Query(default=""),
        limit: int = Query(default=8, ge=1, le=30),
    ):
        owner = _require_admin(request)
        workspace = _resolve_workspace(path)
        status = {"path": workspace, **_git_workspace_status(workspace)}
        git_detail = _git_log_snapshot(workspace) if status.get("is_git") else {}
        db = SessionLocal()
        try:
            query = db.query(TaskRun, ScheduledTask).join(ScheduledTask, TaskRun.task_id == ScheduledTask.id)
            if owner:
                query = query.filter(ScheduledTask.owner == owner)
            if session_id:
                query = query.filter(ScheduledTask.session_id == session_id)
            rows = query.order_by(TaskRun.started_at.desc()).limit(limit).all()
            runs = [{
                "id": run.id,
                "task_id": task.id,
                "task_name": task.name,
                "session_id": task.session_id,
                "status": run.status,
                "result": (run.result or "")[:6000],
                "error": (run.error or "")[:2000],
                "model": run.model or task.model,
                "started_at": run.started_at.isoformat() + "Z" if run.started_at else None,
                "finished_at": run.finished_at.isoformat() + "Z" if run.finished_at else None,
            } for run, task in rows]
        finally:
            db.close()

        risks = []
        if status.get("behind"):
            risks.append(f"Branch is {status['behind']} commit(s) behind its upstream")
        if status.get("changed_files", 0) > 40:
            risks.append("Large change set: review in smaller groups before committing")
        if any(item.get("status") == "??" for item in status.get("files", [])):
            risks.append("Untracked files are present and are not included in Git checkpoints")
        if status.get("is_git") and not status.get("upstream"):
            risks.append("Current branch has no configured upstream")
        return {
            "generated_at": _utc_iso(),
            "workspace": workspace,
            "git": {**status, **git_detail},
            "runs": runs,
            "checkpoints": _list_checkpoints(workspace),
            "worktrees": _worktrees(workspace),
            "risks": risks,
        }

    @router.get("/project")
    def project_config(request: Request, path: str = Query(default="")):
        _require_admin(request)
        return _load_project_config(_resolve_workspace(path))

    @router.put("/project")
    def save_project_config(request: Request, body: ProjectConfigUpdate, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        payload = body.model_dump()
        payload["protected_paths"] = [str(value).strip() for value in payload["protected_paths"] if str(value).strip()][:200]
        payload["permission_rules"] = [str(value).strip() for value in payload["permission_rules"] if str(value).strip()][:200]
        return _atomic_save_project_config(workspace, payload)

    @router.get("/checkpoints")
    def checkpoints(request: Request, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        return {"workspace": workspace, "checkpoints": _list_checkpoints(workspace)}

    @router.post("/checkpoints")
    def create_checkpoint(request: Request, body: CheckpointCreate, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        return {"workspace": workspace, "checkpoint": _create_checkpoint(workspace, body.label)}

    @router.post("/checkpoints/restore")
    def restore_checkpoint(request: Request, body: CheckpointRestore, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        if body.confirmation != "RESTORE":
            raise HTTPException(status_code=400, detail="Type RESTORE to confirm")
        checkpoint = next((row for row in _list_checkpoints(workspace) if row["id"] == body.checkpoint_id), None)
        if not checkpoint:
            raise HTTPException(status_code=404, detail="Checkpoint not found")
        safety = _create_checkpoint(workspace, "before-restore")
        git = shutil.which("git")
        try:
            proc = _run_git(git, workspace, "restore", f"--source={checkpoint['sha']}", "--staged", "--worktree", "--", ".")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Restore timed out")
        if proc.returncode != 0:
            raise HTTPException(status_code=409, detail=(proc.stderr or "Restore failed").strip())
        return {"ok": True, "workspace": workspace, "restored": checkpoint, "safety_checkpoint": safety}

    @router.get("/worktrees")
    def list_worktrees(request: Request, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        return {"workspace": workspace, "worktrees": _worktrees(workspace)}

    @router.post("/worktrees")
    def create_worktree(request: Request, body: WorktreeCreate, path: str = Query(default="")):
        _require_admin(request)
        workspace = _resolve_workspace(path)
        status = _git_workspace_status(workspace)
        if not status.get("is_git"):
            raise HTTPException(status_code=409, detail="Workspace is not a Git repository")
        slug = _checkpoint_slug(body.name).lower()
        if not slug:
            raise HTTPException(status_code=400, detail="Invalid worktree name")
        repo = Path(workspace).resolve()
        parent = repo.parent / f"{repo.name}-worktrees"
        destination = (parent / slug).resolve()
        if parent.resolve() not in destination.parents or destination.exists():
            raise HTTPException(status_code=409, detail="Worktree destination already exists or is invalid")
        branch = f"odysseus/{slug}"
        git = shutil.which("git")
        parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = _safe_command([git, "-c", f"safe.directory={workspace}", "worktree", "add", "-b", branch, str(destination), body.base], workspace, timeout=30)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Worktree creation timed out")
        if proc.returncode != 0:
            raise HTTPException(status_code=409, detail=(proc.stderr or proc.stdout or "Worktree creation failed").strip())
        return {"ok": True, "workspace": workspace, "worktree": {"name": body.name, "path": str(destination), "branch": branch, "base": body.base}}

    return router
