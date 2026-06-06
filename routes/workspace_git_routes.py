from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user
from src.workspace_git import (
    GitWorkspaceError,
    git_blame,
    git_branches,
    git_checkout,
    git_clone,
    git_commit,
    git_commit_selected,
    git_conflicts,
    git_create_branch,
    git_diff,
    git_discard,
    git_history,
    git_init,
    git_remote_action,
    git_resolve_conflict,
    git_stage,
    git_stage_hunk,
    git_status,
    git_unstage,
    git_unstage_hunk,
)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "code": code}, status_code=status_code)


def _authorized(request: Request) -> JSONResponse | None:
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        return _error(403, "not_authorized", "Workspace Git APIs are admin-only")
    return None


def _run(request: Request, func: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any):
    blocked = _authorized(request)
    if blocked is not None:
        return blocked
    try:
        return func(*args, **kwargs)
    except GitWorkspaceError as exc:
        status = 403 if exc.code == "not_authorized" else 400
        return _error(status, exc.code, exc.message)


def _body_list(body: dict[str, Any], key: str) -> list[str]:
    value = body.get(key)
    return value if isinstance(value, list) else []


def _hunk_id(body: dict[str, Any]) -> str:
    return str(body.get("hunkId") or body.get("hunk_id") or "")


def setup_workspace_git_routes() -> APIRouter:
    router = APIRouter(prefix="/api/workspace/git", tags=["workspace-git"])

    @router.get("/status")
    def status(request: Request, workspace: str | None = Query(default=None)):
        return _run(request, git_status, workspace)

    @router.get("/diff")
    def diff(
        request: Request,
        workspace: str | None = Query(default=None),
        path: str | None = Query(default=None),
        staged: bool = Query(default=False),
    ):
        return _run(request, git_diff, workspace, path, staged)

    @router.post("/stage")
    def stage(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_stage, body.get("workspace"), _body_list(body, "paths"))

    @router.post("/unstage")
    def unstage(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_unstage, body.get("workspace"), _body_list(body, "paths"))

    @router.post("/discard")
    def discard(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(
            request,
            git_discard,
            body.get("workspace"),
            _body_list(body, "paths"),
            bool(body.get("confirmConflict") or body.get("confirm_conflict")),
        )

    @router.post("/hunks/stage")
    def stage_hunk(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_stage_hunk, body.get("workspace"), body.get("path"), _hunk_id(body))

    @router.post("/hunks/unstage")
    def unstage_hunk(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_unstage_hunk, body.get("workspace"), body.get("path"), _hunk_id(body))

    @router.post("/commit")
    def commit(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_commit, body.get("workspace"), body.get("message"))

    @router.post("/commit-selected")
    def commit_selected(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_commit_selected, body.get("workspace"), _body_list(body, "paths"), body.get("message"))

    @router.get("/branches")
    def branches(request: Request, workspace: str | None = Query(default=None)):
        return _run(request, git_branches, workspace)

    @router.post("/checkout")
    def checkout(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_checkout, body.get("workspace"), body.get("branch"), stash=False)

    @router.post("/checkout-stash")
    def checkout_stash(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_checkout, body.get("workspace"), body.get("branch"), stash=True)

    @router.post("/branch/create")
    def branch_create(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_create_branch, body.get("workspace"), body.get("branch"))

    @router.post("/fetch")
    def fetch(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_remote_action, body.get("workspace"), "fetch")

    @router.post("/pull")
    def pull(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_remote_action, body.get("workspace"), "pull")

    @router.post("/push")
    def push(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_remote_action, body.get("workspace"), "push")

    @router.post("/init")
    def init(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_init, body.get("workspace"))

    @router.post("/clone")
    def clone(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        return _run(request, git_clone, body.get("workspace"), body.get("url"), body.get("target"), body.get("name"))

    @router.get("/history")
    def history(
        request: Request,
        workspace: str | None = Query(default=None),
        path: str | None = Query(default=None),
        limit: int = Query(default=50),
    ):
        return _run(request, git_history, workspace, path, limit)

    @router.get("/blame")
    def blame(request: Request, workspace: str | None = Query(default=None), path: str | None = Query(default=None)):
        return _run(request, git_blame, workspace, path)

    @router.get("/conflicts")
    def conflicts(request: Request, workspace: str | None = Query(default=None)):
        return _run(request, git_conflicts, workspace)

    @router.post("/conflict/resolve")
    def resolve_conflict(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        blocked = _authorized(request)
        if blocked is not None:
            return blocked
        if "content" not in body or not isinstance(body.get("content"), str):
            return _error(400, "invalid_request", "content must be provided as text")
        try:
            return git_resolve_conflict(body.get("workspace"), body.get("path"), body.get("content"))
        except GitWorkspaceError as exc:
            status = 403 if exc.code == "not_authorized" else 400
            return _error(status, exc.code, exc.message)

    return router
