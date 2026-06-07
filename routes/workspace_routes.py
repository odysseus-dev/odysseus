"""Workspace API — browse and edit files in a selected tool workspace."""
import os
from typing import Any

from fastapi import APIRouter, Body, Request, HTTPException, Query
from fastapi.responses import JSONResponse

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user
from src.workspace_git import (
    GitWorkspaceError,
    delete_workspace_file,
    list_workspace_files,
    read_workspace_file,
    save_workspace_file,
)


def _workspace_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "code": code}, status_code=status_code)


def _require_workspace_admin(request: Request) -> JSONResponse | None:
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        return _workspace_error(403, "not_authorized", "Workspace APIs are admin-only")
    return None


def _run_workspace(request: Request, func, *args):
    blocked = _require_workspace_admin(request)
    if blocked is not None:
        return blocked
    try:
        return func(*args)
    except GitWorkspaceError as exc:
        return _workspace_error(400, exc.code, exc.message)


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

    @router.get("/files")
    def files(request: Request, workspace: str | None = Query(default=None), path: str = Query(default="")):
        return _run_workspace(request, list_workspace_files, workspace, path)

    @router.get("/file")
    def file(request: Request, workspace: str | None = Query(default=None), path: str = Query(default="")):
        return _run_workspace(request, read_workspace_file, workspace, path)

    @router.post("/file/save")
    def file_save(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        blocked = _require_workspace_admin(request)
        if blocked is not None:
            return blocked
        if "content" not in body or not isinstance(body.get("content"), str):
            return _workspace_error(400, "invalid_request", "content must be provided as text")
        try:
            return save_workspace_file(body.get("workspace"), body.get("path") or "", body.get("content"))
        except GitWorkspaceError as exc:
            return _workspace_error(400, exc.code, exc.message)

    @router.post("/file/delete")
    def file_delete(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        blocked = _require_workspace_admin(request)
        if blocked is not None:
            return blocked
        try:
            return delete_workspace_file(body.get("workspace"), body.get("path") or "")
        except GitWorkspaceError as exc:
            return _workspace_error(400, exc.code, exc.message)

    return router
