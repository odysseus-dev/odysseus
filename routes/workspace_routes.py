"""Workspace API — browse server directories to pick a tool workspace folder."""
import os
import shutil
import subprocess
from fastapi import APIRouter, Request, HTTPException, Query, Form

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user


def setup_workspace_routes():
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List subdirectories of `path` (default: home) so the UI can navigate
        the server filesystem and pick a workspace folder. Directories only.

        ADMIN-ONLY: this enumerates the server filesystem, so it is gated the
        same way the file/shell tools are (read_file/write_file/bash are in
        NON_ADMIN_BLOCKED_TOOLS). A non-admin who can't use those tools must not
        be able to map the host's directory tree either.
        """
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Workspace browsing is admin-only")

        # Resolve symlinks so the reported path is canonical and the UI navigates
        # real directories (defends against symlink games in displayed paths).
        target = os.path.realpath(os.path.expanduser(path.strip() or "~"))
        if not os.path.isdir(target):
            target = os.path.realpath(os.path.expanduser("~"))

        dirs = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        # Don't follow symlinks when classifying — a symlinked
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

        parent = os.path.dirname(target)
        return {
            "path": target,
            "parent": parent if parent and parent != target else None,
            "dirs": sorted(dirs, key=lambda d: d["name"].lower()),
        }

    @router.post("/git")
    async def git_exec(request: Request, command: str = Form(...), path: str = Form("")):
        """Run a git command in the workspace from the chat input (`/git`).

        Reuses the agent `git` tool's implementation — same subcommand allowlist,
        confinement, and injected commit identity — so it's a thin, no-LLM git
        client scoped to the chosen repo. Admin-only (it runs git on the host).
        """
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="git is admin-only")
        ws = os.path.realpath(os.path.expanduser((path or "").strip()))
        if not path.strip() or not os.path.isdir(ws):
            raise HTTPException(status_code=400, detail="A valid workspace folder is required")
        # Must be an actual git worktree, not just any server directory the
        # browser can name — same confinement premise as the agent git tool.
        git_bin = shutil.which("git")
        if not git_bin:
            raise HTTPException(status_code=500, detail="git is not installed on the server")
        try:
            probe = subprocess.run(
                [git_bin, "-C", ws, "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            probe = None
        if not probe or probe.returncode != 0 or (probe.stdout or "").strip() != "true":
            raise HTTPException(status_code=400, detail="Not a git repository")
        from src.tool_execution import _direct_fallback
        result = await _direct_fallback("git", command, workspace=ws)
        return result or {"error": "git: execution failed", "exit_code": 1}

    return router
