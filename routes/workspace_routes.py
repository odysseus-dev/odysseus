"""Workspace API - browse server directories to pick a tool workspace folder."""
import os
import re
import shutil
import subprocess
from fastapi import APIRouter, Request, HTTPException, Query

from src.auth_helpers import get_current_user
from src.runtime_paths import get_app_root
from src.tool_security import owner_is_admin_or_single_user

# Cap entries returned per directory (mirrors filesystem_tools._CODENAV_MAX_HITS).
# A huge directory shouldn't dump thousands of rows into the picker; the user can
# type/paste a path to jump straight in instead.
_MAX_BROWSE_DIRS = 500
_MAX_PROJECTS = 40
_MAX_STATUS_FILES = 80


def _empty_git_status() -> dict:
    return {
        "is_git": False,
        "branch": "",
        "changed_files": 0,
        "ahead": 0,
        "behind": 0,
        "additions": 0,
        "deletions": 0,
        "upstream": "",
        "files": [],
    }


def _run_git(git: str, path: str, *args: str):
    kwargs = {
        "cwd": path,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 4,
        "check": False,
        "env": {**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run([git, "-c", f"safe.directory={path}", *args], **kwargs)


def _git_workspace_status(path: str) -> dict:
    """Return a read-only Git summary for a vetted workspace."""
    git = shutil.which("git")
    if not git:
        return _empty_git_status()
    try:
        proc = _run_git(git, path, "status", "--porcelain=v1", "--branch", "--untracked-files=normal")
    except (OSError, subprocess.TimeoutExpired):
        return _empty_git_status()
    if proc.returncode != 0:
        return _empty_git_status()

    lines = proc.stdout.splitlines()
    header = lines[0][3:].strip() if lines and lines[0].startswith("## ") else ""
    branch = header
    for prefix in ("No commits yet on ", "Initial commit on "):
        if branch.startswith(prefix):
            branch = branch[len(prefix):]
    branch = branch.split("...", 1)[0].split(" [", 1)[0].strip()
    ahead_match = re.search(r"\bahead (\d+)\b", header)
    behind_match = re.search(r"\bbehind (\d+)\b", header)
    files = []
    for line in lines:
        if not line or line.startswith("## ") or len(line) < 3:
            continue
        code = line[:2]
        file_path = line[3:].strip()
        if " -> " in file_path:
            file_path = file_path.split(" -> ", 1)[1]
        files.append({
            "path": file_path.strip('"'),
            "status": code.strip() or "M",
            "staged": code[0] not in (" ", "?"),
        })

    additions = 0
    deletions = 0
    for diff_args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
        try:
            diff = _run_git(git, path, *diff_args)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if diff.returncode != 0:
            continue
        for line in diff.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            if parts[0].isdigit():
                additions += int(parts[0])
            if parts[1].isdigit():
                deletions += int(parts[1])

    upstream = ""
    try:
        upstream_proc = _run_git(git, path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if upstream_proc.returncode == 0:
            upstream = upstream_proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "is_git": True,
        "branch": branch,
        "changed_files": len(files),
        "ahead": int(ahead_match.group(1)) if ahead_match else 0,
        "behind": int(behind_match.group(1)) if behind_match else 0,
        "additions": additions,
        "deletions": deletions,
        "upstream": upstream,
        "files": files[:_MAX_STATUS_FILES],
    }


def _default_workspace():
    """Return a useful, vetted coding root without hard-coding one machine.

    An explicit environment setting wins.  Source checkouts otherwise use the
    parent of the Odysseus repository, which makes sibling projects immediately
    available in the Projects sidebar.  The repository itself and the user's
    home directory are conservative fallbacks for packaged installations.
    """
    from src.tool_execution import vet_workspace

    configured = os.environ.get("ODYSSEUS_DEFAULT_WORKSPACE", "").strip()
    app_root = os.path.realpath(get_app_root())
    candidates = [configured]
    if os.path.basename(app_root).casefold() == "odysseus":
        candidates.append(os.path.dirname(app_root))
    candidates.extend([app_root, os.getcwd(), os.path.expanduser("~")])
    for candidate in candidates:
        if not candidate:
            continue
        resolved = vet_workspace(candidate)
        if resolved:
            return resolved
    return None


def setup_workspace_routes():
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    def require_workspace_access(request: Request):
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Workspace selection is admin-only")

    @router.get("/default")
    def default_workspace(request: Request):
        """Return the server's persistent default coding workspace."""
        require_workspace_access(request)
        path = _default_workspace()
        if not path:
            raise HTTPException(status_code=404, detail="No usable default workspace found")
        return {"path": path}

    @router.get("/projects")
    def projects(request: Request):
        """List immediate project folders below the default coding root."""
        require_workspace_access(request)
        root = _default_workspace()
        if not root:
            return {"root": None, "projects": []}
        found = []
        try:
            with os.scandir(root) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            found.append({"name": entry.name, "path": os.path.join(root, entry.name)})
                    except OSError:
                        continue
        except (PermissionError, OSError):
            pass
        found.sort(key=lambda item: item["name"].casefold())
        return {"root": root, "projects": found[:_MAX_PROJECTS]}

    @router.get("/status")
    def workspace_status(request: Request, path: str = Query(default="")):
        """Return branch and dirty-file counts for a selected coding workspace."""
        require_workspace_access(request)
        from src.tool_execution import vet_workspace
        resolved = vet_workspace(path)
        if not resolved:
            raise HTTPException(status_code=400, detail="Invalid workspace")
        return {"path": resolved, **_git_workspace_status(resolved)}

    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List subdirectories of `path` (default: home) so the UI can navigate
        the server filesystem and pick a workspace folder. Directories only.

        ADMIN-ONLY: this enumerates the server filesystem, so it is gated the
        same way the file/shell tools are (read_file/write_file/bash are in
        NON_ADMIN_BLOCKED_TOOLS). A non-admin who can't use those tools must not
        be able to map the host's directory tree either.
        """
        require_workspace_access(request)

        # Resolve symlinks so the reported path is canonical and the UI navigates
        # real directories (defends against symlink games in displayed paths).
        target = os.path.realpath(os.path.expanduser(path.strip() or _default_workspace() or "~"))
        if not os.path.isdir(target):
            target = os.path.realpath(os.path.expanduser("~"))

        dirs = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        # Don't follow symlinks when classifying - a symlinked
                        # dir is skipped rather than letting the browser wander
                        # off via a link. Hidden entries are omitted.
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            # Build the child path server-side with os.path.join
                            # so it's correct on Windows (backslashes) and Linux.
                            dirs.append({"name": entry.name, "path": os.path.join(target, entry.name)})
                    except OSError:
                        continue
        except (PermissionError, OSError):
            dirs = []

        dirs_sorted = sorted(dirs, key=lambda d: d["name"].lower())
        truncated = len(dirs_sorted) > _MAX_BROWSE_DIRS
        parent = os.path.dirname(target)
        from src.tool_execution import vet_workspace
        return {
            "path": target,
            "parent": parent if parent and parent != target else None,
            "dirs": dirs_sorted[:_MAX_BROWSE_DIRS],
            "truncated": truncated,
            # Whether this directory may be bound as a workspace (filesystem
            # roots and sensitive dirs may be browsed through but not chosen).
            "selectable": vet_workspace(target) is not None,
        }

    @router.get("/vet")
    def vet(request: Request, path: str = Query(default="")):
        """Validate a workspace path without binding it.

        The UI calls this before persisting a manually typed path (/workspace
        set) so a typo, file path, deleted folder, sensitive dir, or filesystem
        root is rejected up front with the canonical path returned on success,
        instead of being stored client-side and silently dropped at chat time.
        Admin-gated like /browse: it confirms path existence on the host.
        """
        require_workspace_access(request)
        from src.tool_execution import vet_workspace
        resolved = vet_workspace(path)
        return {"ok": resolved is not None, "path": resolved}

    return router
