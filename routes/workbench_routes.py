"""Workbench API — agent observability, repository changes and PR feedback.

One router for what used to be scattered or missing:

* ``/activity`` — the unified agent activity feed (:mod:`src.agent_activity`):
  history, a live SSE stream, and the run registry that lists every Claude
  Code job, sub-agent exchange, shell job and chat turn with its status.
* ``/repo`` — read-only git inspection (:mod:`src.repo_inspect`) of a
  checkout under the approved roots: status, per-file changes, unified diffs,
  file contents at a ref, commits.
* ``/prs`` — the configured repository's pull requests through the same
  GitHub App/PAT credential the worktree publish flow uses: list, detail
  (files, reviews, comments, checks), raw diff, and posting a comment or a
  review.

Everything here is **admin-only** for cookie sessions, like the Claude Code
and workspace routes: it exposes host checkouts and can write to GitHub.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.middleware import require_admin
from src import agent_activity as activity
from src import repo_inspect
from src.auth_helpers import require_user

logger = logging.getLogger(__name__)


def _admin(request: Request) -> str:
    owner = require_user(request)
    require_admin(request)
    return owner


def _repo_error(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc)[:500])


def _pr_context():
    """(cfg, blockers) for the PR feedback surface."""
    from src.agent_worktree.config import load_config
    from src.agent_worktree.github import pr_access_blockers

    cfg = load_config()
    return cfg, pr_access_blockers(cfg)


async def _pr_token(cfg):
    from src.agent_worktree.github import GitHubError, resolve_token

    try:
        return await resolve_token(cfg)
    except GitHubError as exc:
        raise HTTPException(502, f"GitHub credential problem: {exc}")


def setup_workbench_routes() -> APIRouter:
    router = APIRouter(prefix="/api/workbench", tags=["workbench"])

    # ── activity ────────────────────────────────────────────────────────────

    @router.get("/activity")
    async def activity_history(request: Request, session_id: str = Query(...),
                               since: int = Query(default=0), limit: int = Query(default=200)):
        _admin(request)
        return {
            "session_id": session_id,
            "events": activity.history(session_id, since_seq=max(0, since), limit=limit),
            "seq": activity.last_seq(session_id),
        }

    @router.get("/activity/stream")
    async def activity_stream(request: Request, session_id: str = Query(default=activity.GLOBAL_FEED),
                              since: int = Query(default=0)):
        """Server-sent events: replay after ``since``, then live. Pass
        ``session_id=*`` for every session."""
        _admin(request)
        gen = activity.subscribe(session_id or activity.GLOBAL_FEED, since_seq=max(0, since))
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/runs")
    async def runs(request: Request, session_id: Optional[str] = None, limit: int = 50,
                   active: bool = False):
        _admin(request)
        return {"runs": activity.list_runs(session_id=session_id, limit=limit, active_only=active)}

    @router.get("/runs/{run_id}")
    async def run_detail(request: Request, run_id: str):
        _admin(request)
        rec = activity.get_run(run_id)
        if rec is None:
            raise HTTPException(404, "Run not found")
        rec["events"] = activity.run_events(run_id)
        return rec

    @router.post("/runs/{run_id}/stop")
    async def stop_run(request: Request, run_id: str):
        """Stop one unit of delegated work without stopping the chat turn
        that started it: a sub-agent (its partial answer still reaches the
        parent), a Claude Code background task, a background shell job, or a
        chat turn itself."""
        _admin(request)
        from src.agent_control import stop_run

        try:
            return await stop_run(run_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    # ── repository inspection ───────────────────────────────────────────────

    @router.get("/repo/roots")
    async def repo_roots(request: Request):
        """Inspectable roots and the checkouts found under them, so the UI can
        offer a repository picker without a free-text path."""
        _admin(request)
        repos = []
        try:
            from src.agent_tools.claude_code_tools import discover_repositories

            repos = discover_repositories(limit=50)
        except Exception as exc:  # the picker degrades to the raw roots
            logger.debug("workbench: repository discovery failed: %s", exc)
        workspace = None
        try:
            from src.tool_execution import get_active_workspace

            workspace = get_active_workspace()
        except Exception:
            pass
        seen = {r["path"] for r in repos}
        try:
            from src.agent_worktree.config import load_config

            cfg = load_config()
            for candidate in (cfg.source_repo, workspace):
                if candidate and candidate not in seen:
                    try:
                        repo_inspect.resolve_repo(candidate)
                    except repo_inspect.RepoError:
                        continue
                    repos.append({"path": candidate, "root": None, "branch": None})
                    seen.add(candidate)
        except Exception:
            pass
        return {"roots": repo_inspect.allowed_roots(), "repositories": repos, "workspace": workspace}

    @router.get("/repo/status")
    async def repo_status(request: Request, path: str = Query(...)):
        _admin(request)
        try:
            return await repo_inspect.status(path)
        except repo_inspect.RepoError as exc:
            raise _repo_error(exc)

    @router.get("/repo/changes")
    async def repo_changes(request: Request, path: str = Query(...), base: Optional[str] = None):
        _admin(request)
        try:
            return await repo_inspect.changes(path, base=base)
        except repo_inspect.RepoError as exc:
            raise _repo_error(exc)

    @router.get("/repo/diff")
    async def repo_diff(request: Request, path: str = Query(...), file: str = Query(...),
                        base: Optional[str] = None, commit: Optional[str] = None):
        _admin(request)
        try:
            return await repo_inspect.file_diff(path, file, base=base, commit=commit)
        except repo_inspect.RepoError as exc:
            raise _repo_error(exc)

    @router.get("/repo/file")
    async def repo_file(request: Request, path: str = Query(...), file: str = Query(...),
                        ref: str = Query(default="HEAD")):
        _admin(request)
        try:
            return await repo_inspect.file_at(path, file, ref=ref)
        except repo_inspect.RepoError as exc:
            raise _repo_error(exc)

    @router.get("/repo/commits")
    async def repo_commits(request: Request, path: str = Query(...), limit: int = 50,
                           base: Optional[str] = None):
        _admin(request)
        try:
            return {"commits": await repo_inspect.commits(path, limit=limit, base=base)}
        except repo_inspect.RepoError as exc:
            raise _repo_error(exc)

    @router.get("/repo/commit")
    async def repo_commit(request: Request, path: str = Query(...), sha: str = Query(...)):
        _admin(request)
        try:
            return await repo_inspect.commit_detail(path, sha)
        except repo_inspect.RepoError as exc:
            raise _repo_error(exc)

    # ── pull requests ───────────────────────────────────────────────────────

    @router.get("/prs/config")
    async def prs_config(request: Request):
        _admin(request)
        cfg, blockers = _pr_context()
        return {
            "configured": not blockers,
            "repo": cfg.repo_slug or None,
            "base_branch": cfg.base_branch,
            "credential": "github_app" if cfg.has_github_app else ("fallback_token" if cfg.has_any_credential else None),
            "blockers": blockers,
        }

    @router.get("/prs")
    async def prs_list(request: Request, state: str = "open", limit: int = 30):
        _admin(request)
        from src.agent_worktree import github as gh

        cfg, blockers = _pr_context()
        if blockers:
            raise HTTPException(409, "; ".join(blockers))
        token = await _pr_token(cfg)
        try:
            return {"repo": cfg.repo_slug, "pulls": await gh.list_pull_requests(cfg, token, state=state, limit=limit)}
        except gh.GitHubError as exc:
            raise HTTPException(502, str(exc))

    @router.get("/prs/{number}")
    async def prs_detail(request: Request, number: int):
        """Everything the review view needs in one round trip."""
        _admin(request)
        from src.agent_worktree import github as gh

        cfg, blockers = _pr_context()
        if blockers:
            raise HTTPException(409, "; ".join(blockers))
        token = await _pr_token(cfg)
        try:
            pr = await gh.get_pull_request(cfg, token, number)
            out: Dict[str, Any] = {"repo": cfg.repo_slug, "pull": pr}
            out["files"] = await gh.pull_request_files(cfg, token, number)
            out["reviews"] = await gh.pull_request_reviews(cfg, token, number)
            out["review_comments"] = await gh.pull_request_review_comments(cfg, token, number)
            out["comments"] = await gh.pull_request_comments(cfg, token, number)
            out["checks"] = await gh.pull_request_checks(cfg, token, (pr.get("head") or {}).get("sha") or "")
            return out
        except gh.GitHubError as exc:
            raise HTTPException(502, str(exc))

    @router.get("/prs/{number}/diff")
    async def prs_diff(request: Request, number: int):
        _admin(request)
        from src.agent_worktree import github as gh

        cfg, blockers = _pr_context()
        if blockers:
            raise HTTPException(409, "; ".join(blockers))
        token = await _pr_token(cfg)
        try:
            return await gh.pull_request_diff(cfg, token, number)
        except gh.GitHubError as exc:
            raise HTTPException(502, str(exc))

    @router.post("/prs/{number}/comment")
    async def prs_comment(request: Request, number: int, body: Dict[str, Any] = Body(default_factory=dict)):
        owner = _admin(request)
        from src.agent_worktree import github as gh

        cfg, blockers = _pr_context()
        if blockers:
            raise HTTPException(409, "; ".join(blockers))
        token = await _pr_token(cfg)
        try:
            out = await gh.create_issue_comment(cfg, token, number, str(body.get("body") or ""))
        except gh.GitHubError as exc:
            raise HTTPException(400 if "required" in str(exc) else 502, str(exc))
        activity.publish(body.get("session_id"), "note", f"PR #{number}: comment posted", source="worktree",
                         owner=owner, data={"pull_request": number, "url": out.get("url")})
        return out

    @router.post("/prs/{number}/review")
    async def prs_review(request: Request, number: int, body: Dict[str, Any] = Body(default_factory=dict)):
        owner = _admin(request)
        from src.agent_worktree import github as gh

        cfg, blockers = _pr_context()
        if blockers:
            raise HTTPException(409, "; ".join(blockers))
        token = await _pr_token(cfg)
        try:
            out = await gh.create_review(cfg, token, number, event=str(body.get("event") or "COMMENT"),
                                         body=str(body.get("body") or ""), comments=body.get("comments") or [])
        except gh.GitHubError as exc:
            raise HTTPException(400 if ("required" in str(exc) or "must be" in str(exc)) else 502, str(exc))
        activity.publish(body.get("session_id"), "note", f"PR #{number}: review submitted ({out.get('state')})",
                         source="worktree", owner=owner, data={"pull_request": number, "url": out.get("url")})
        return out

    return router
