r"""Gnexus App Dock read-only scanner.

Discovers app/tool candidates under C:\Users\iamcy\CymaticsDev without executing
commands, installing packages, or mutating project contents.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE_ROOT = os.getenv("GNEXUS_WORKSPACE_ROOT", r"C:\Users\iamcy\CymaticsDev")
SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".next", "dist",
    "build", "logs", "data", ".mypy_cache", ".pytest_cache", ".cache"
}

@dataclass
class AppCandidate:
    id: str
    name: str
    root: str
    relativePath: str
    type: str
    confidence: str
    signals: list[str]
    commands: dict[str, str]
    urls: list[str]
    launchApprovalRequired: bool = True
    runtimeStartEnabled: bool = False

def _safe_id(text: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return out[:80] or "app"

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _classify(path: Path, signals: list[str]) -> tuple[str, dict[str, str], list[str], str]:
    commands: dict[str, str] = {}
    urls: list[str] = []
    app_type = "generic-tool"
    confidence = "low"

    if (path / "START.bat").exists():
        commands["start"] = "START.bat"
        app_type = "gnexus-droppack-or-local-tool"
        confidence = "medium"
    if (path / "VERIFY.bat").exists():
        commands["verify"] = "VERIFY.bat"

    package = path / "package.json"
    if package.exists():
        data = _read_json(package)
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        if scripts.get("dev"):
            commands["start"] = "npm run dev"
        elif scripts.get("start"):
            commands["start"] = "npm start"
        if scripts.get("build"):
            commands["verify"] = "npm run build"
        if any((path / n).exists() for n in ("vite.config.js", "vite.config.ts", "vite.config.mjs")):
            app_type = "vite-app"
            urls.append("http://127.0.0.1:5173")
        elif any((path / n).exists() for n in ("next.config.js", "next.config.mjs", "next.config.ts")):
            app_type = "next-app"
            urls.append("http://127.0.0.1:3000")
        else:
            app_type = "node-app"
        confidence = "high"

    if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists():
        if (path / "app.py").exists():
            commands.setdefault("start", "python -m uvicorn app:app --host 127.0.0.1 --port 8000")
            urls.append("http://127.0.0.1:8000")
            app_type = "python-fastapi-or-asgi"
            confidence = "medium"
        elif (path / "main.py").exists():
            commands.setdefault("start", "python main.py")
            app_type = "python-app"
            confidence = "medium"
        else:
            app_type = "python-project"
            confidence = "medium"

    if (path / "docker-compose.yml").exists() or (path / "docker-compose.yaml").exists():
        commands.setdefault("containerStartCandidate", "docker compose up")
        app_type = app_type + "+docker" if app_type != "generic-tool" else "docker-compose-app"
        confidence = "medium" if confidence == "low" else confidence

    return app_type, commands, urls, confidence

def _signals(path: Path) -> list[str]:
    signals: list[str] = []
    names = {p.name for p in path.iterdir()} if path.exists() else set()
    for exact in ("package.json", "pyproject.toml", "requirements.txt", "app.py", "main.py", "START.bat", "VERIFY.bat", "docker-compose.yml", "docker-compose.yaml"):
        if exact in names:
            signals.append(exact)
    for p in path.iterdir() if path.exists() else []:
        n = p.name.lower()
        if n.startswith("vite.config"):
            signals.append(p.name)
        if n.startswith("next.config"):
            signals.append(p.name)
    return sorted(set(signals))

def _iter_dirs(root: Path, max_depth: int):
    root = root.resolve()
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth:
            continue
        try:
            children = [p for p in current.iterdir() if p.is_dir()]
        except Exception:
            continue
        for child in reversed(children):
            if child.name in SKIP_DIRS:
                continue
            if child.name.startswith(".") and child.name not in {".gnexus", ".gnx"}:
                continue
            stack.append((child, depth + 1))

def scan_workspace(workspace_root: str | None = None, max_depth: int = 5) -> dict[str, Any]:
    root = Path(workspace_root or DEFAULT_WORKSPACE_ROOT)
    candidates: list[AppCandidate] = []
    seen_ids: dict[str, int] = {}

    if not root.exists():
        return {
            "schema": "gnexus.app-registry.v1",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "workspaceRoot": str(root),
            "status": "WORKSPACE_NOT_FOUND",
            "apps": [],
        }

    for path, _depth in _iter_dirs(root, max_depth):
        sigs = _signals(path)
        if not sigs:
            continue
        app_type, commands, urls, confidence = _classify(path, sigs)
        rel = str(path.relative_to(root)) if path != root else "."
        base_id = _safe_id(rel.replace("\\", "-").replace("/", "-"))
        count = seen_ids.get(base_id, 0)
        seen_ids[base_id] = count + 1
        app_id = base_id if count == 0 else f"{base_id}-{count+1}"
        candidates.append(AppCandidate(
            id=app_id,
            name=path.name,
            root=str(path),
            relativePath=rel,
            type=app_type,
            confidence=confidence,
            signals=sigs,
            commands=commands,
            urls=urls,
        ))

    return {
        "schema": "gnexus.app-registry.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "workspaceRoot": str(root),
        "status": "SCAN_COMPLETE",
        "appCount": len(candidates),
        "apps": [asdict(c) for c in candidates],
    }

def write_registry(repo_root: str, workspace_root: str | None = None, max_depth: int = 5) -> dict[str, Any]:
    repo = Path(repo_root)
    registry = scan_workspace(workspace_root, max_depth=max_depth)
    data_dir = repo / "data" / "gnexus"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "app-registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")

    state = {
        "schema": "gnexus.app-dock-state.v1",
        "status": "JUNIPERUS_APP_DOCK_RUNTIME_LAUNCHER_READY_LOCAL",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "workspaceRoot": registry.get("workspaceRoot"),
        "appCount": registry.get("appCount", 0),
        "runtimeStartEnabled": False,
        "launchApprovalRequired": True,
        "appDockUrl": "http://127.0.0.1:7010/gnexus/app-dock",
    }
    mc = data_dir / "mission-control"
    mc.mkdir(parents=True, exist_ok=True)
    (mc / "app-dock-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    app_dock = data_dir / "app-dock"
    app_dock.mkdir(parents=True, exist_ok=True)
    for name, default in {
        "launch-queue.json": {"schema": "gnexus.launch-queue.v1", "items": []},
        "runtime-sessions.json": {"schema": "gnexus.runtime-sessions.v1", "items": []},
    }.items():
        p = app_dock / name
        if not p.exists():
            p.write_text(json.dumps(default, indent=2), encoding="utf-8")

    return registry
