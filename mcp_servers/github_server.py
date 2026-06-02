"""
github_server.py

MCP server exposing GitHub tools (read + gated write). Authenticated with a
per-user Personal Access Token stored encrypted in
`github_integrations.pat_encrypted` and decrypted at request time.

Routing (per-call, multi-tenant): the server is a single process-wide stdio
instance shared by all Odysseus users, so each tool call must say whose PAT to
use. The trusted dispatch layer (src/tool_execution.execute_tool_block) stamps
the authenticated session owner onto every github call as `_owner`; call_tool
pops it and threads it explicitly through `_dispatch` → `_gh_request` /
`_git_push_subprocess`. Identity is therefore per-call and passed by argument
(never module/global state), so concurrent calls from different users can't
cross PATs.

If a caller doesn't stamp `_owner` (any non-agent path), we fall back to
inferring the owner from the DB — but ONLY when unambiguous (exactly one
enabled integration, the solo self-host case). With two or more enabled
integrations and no `_owner`, `_resolve_owner` raises `AmbiguousOwnerError` and
the call fails CLOSED rather than acting with an arbitrary user's token. The
`ODYSSEUS_GH_OWNER` env var still overrides DB inference if a future spawner
sets it.

Tool gating happens upstream in routes/chat_routes.py: the gh_* read tools are
gated by `allow_github`, and the write tools additionally require
`allow_github_write` + the persisted `write_enabled` setting. By the time a
tool call reaches dispatch here, the chat layer has already authorized it.
The tools are minimal wrappers; trimming helpers keep responses small.
"""

import json
import os
import re
import subprocess
import sys
import sqlite3
from pathlib import Path
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Local imports — resolve only after sys.path tweak above.
try:
    from src.secret_storage import decrypt as _decrypt_secret
except Exception:
    def _decrypt_secret(value: str) -> str:
        return value

server = Server("github")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GH_API = "https://api.github.com"
GH_TIMEOUT = float(os.environ.get("GITHUB_API_TIMEOUT", "20"))
USER_AGENT = "Odysseus-Integration/0.1"


# ── PAT resolution ──
# We load once per process and cache. If the user updates the PAT in settings,
# the MCP server is restarted by the integration routes so a stale cache is
# impossible in normal flow.

_PAT_CACHE: dict = {}  # owner -> pat


def _db_path() -> Path:
    return DATA_DIR / "app.db"


def _load_pat(owner: str) -> str | None:
    if owner in _PAT_CACHE:
        return _PAT_CACHE[owner]
    path = _db_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pat_encrypted, enabled FROM github_integrations WHERE owner = ?",
            (owner,),
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row or not row["enabled"]:
        return None
    raw = row["pat_encrypted"] or ""
    pat = _decrypt_secret(raw) if raw.startswith("enc:") else raw
    if pat:
        _PAT_CACHE[owner] = pat
    return pat


class AmbiguousOwnerError(RuntimeError):
    """Raised when this process-wide server cannot tell whose PAT to use."""


# Sentinel: distinguishes "_owner key absent" (non-agent caller → fall back to
# DB resolution) from "_owner present and empty" (the solo-install owner "").
_OWNER_UNSET = object()


def _resolve_owner() -> str | None:
    """Owner whose PAT this server acts with.

    If ODYSSEUS_GH_OWNER is set (a future per-user spawner can inject it), use
    it verbatim. Otherwise we infer from the DB — but ONLY when the inference is
    unambiguous (exactly one enabled integration, the solo-self-host case).

    If two or more users have enabled the integration and no owner was injected,
    we have no per-call context to tell them apart, so we FAIL CLOSED: raising
    rather than silently acting as — and with the token of — whichever row
    happens to sort first. Acting cross-account with the wrong user's
    credentials is far worse than returning an error."""
    env_owner = os.environ.get("ODYSSEUS_GH_OWNER")
    if env_owner:
        return env_owner
    path = _db_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        rows = conn.execute(
            "SELECT owner FROM github_integrations WHERE enabled = 1"
        ).fetchall()
        conn.close()
    except Exception:
        return None
    enabled = [r[0] for r in rows]
    if not enabled:
        return None
    if len(enabled) > 1:
        raise AmbiguousOwnerError(
            "Multiple Odysseus users have enabled the GitHub integration, but "
            "this server instance has no per-user context (ODYSSEUS_GH_OWNER is "
            "unset), so it cannot tell whose PAT to use. Refusing to act with an "
            "ambiguous identity. Per-user GitHub routing is not supported in this "
            "deployment mode — keep a single enabled integration, or set "
            "ODYSSEUS_GH_OWNER for this process."
        )
    return enabled[0]


async def _gh_request(
    method: str,
    path: str,
    *,
    owner: str | None,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict | list | None:
    """Hit the GitHub REST API with `owner`'s PAT.

    `owner` is resolved per-call by the dispatcher (the authenticated Odysseus
    user, or DB fallback for non-agent callers) — never inferred here, so
    concurrent calls from different users can't cross PATs. `owner=""` is a
    valid lookup key (the solo-install owner); only `owner is None` is "no
    owner". Returns parsed JSON. On HTTP errors raises with the upstream
    message so the agent can surface useful failure context."""
    if owner is None:
        raise RuntimeError("No GitHub integration owner found — set one up in Settings → Integrations.")
    pat = _load_pat(owner)
    if not pat:
        raise RuntimeError(f"No GitHub PAT found for user '{owner}' (integration disabled or missing).")
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    url = path if path.startswith("http") else f"{GH_API}{path}"
    async with httpx.AsyncClient(timeout=GH_TIMEOUT) as client:
        resp = await client.request(method, url, params=params, json=json_body, headers=headers)
        if resp.status_code >= 400:
            # Surface GitHub's own error message rather than a generic httpx one.
            try:
                detail = resp.json().get("message", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"GitHub API {resp.status_code} on {method} {path}: {detail}")
        if not resp.content:
            return None
        # Some endpoints return non-JSON (e.g. .diff). Treat .diff specially.
        ctype = resp.headers.get("content-type", "")
        if "application/vnd.github.v3.diff" in ctype or "text/" in ctype:
            return {"_raw_text": resp.text}
        return resp.json()


# ── Tool helpers — keep response bodies trimmed so we don't shove 200KB
# of JSON into the agent's context for every tool call. ──

def _trim_pr(pr: dict) -> dict:
    """A summary of a PR. Drops noisy fields (raw URLs, base/head sha tree,
    permissions, etc.) keeping only what an agent reasoning about it needs."""
    if not isinstance(pr, dict):
        return pr
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),  # open / closed
        "draft": pr.get("draft"),
        "merged": pr.get("merged"),
        "url": pr.get("html_url"),
        "repo": (pr.get("base") or {}).get("repo", {}).get("full_name"),
        "author": (pr.get("user") or {}).get("login"),
        "head_ref": (pr.get("head") or {}).get("ref"),
        "base_ref": (pr.get("base") or {}).get("ref"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "merged_at": pr.get("merged_at"),
        "comments": pr.get("comments"),  # issue-style comments
        "review_comments": pr.get("review_comments"),
        "commits": pr.get("commits"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
        "body": pr.get("body"),
        "labels": [(l or {}).get("name") for l in (pr.get("labels") or [])],
        "requested_reviewers": [(r or {}).get("login") for r in (pr.get("requested_reviewers") or [])],
    }


def _trim_issue(it: dict) -> dict:
    if not isinstance(it, dict):
        return it
    return {
        "number": it.get("number"),
        "title": it.get("title"),
        "state": it.get("state"),
        "url": it.get("html_url"),
        "repo": (it.get("repository_url") or "").rsplit("/", 2)[-2:],
        "author": (it.get("user") or {}).get("login"),
        "created_at": it.get("created_at"),
        "updated_at": it.get("updated_at"),
        "labels": [(l or {}).get("name") for l in (it.get("labels") or [])],
        "is_pr": "pull_request" in it,
        "body": (it.get("body") or "")[:1000],  # cap body so search results stay readable
    }


def _trim_comment(c: dict) -> dict:
    if not isinstance(c, dict):
        return c
    return {
        "id": c.get("id"),
        "author": (c.get("user") or {}).get("login"),
        "created_at": c.get("created_at"),
        "updated_at": c.get("updated_at"),
        "body": c.get("body"),
        "url": c.get("html_url"),
        # review-comment-specific fields (None for issue comments)
        "path": c.get("path"),
        "line": c.get("line") or c.get("original_line"),
        "diff_hunk": (c.get("diff_hunk") or "")[:500] if c.get("diff_hunk") else None,
    }


def _trim_notification(n: dict) -> dict:
    if not isinstance(n, dict):
        return n
    subject = n.get("subject") or {}
    return {
        "id": n.get("id"),
        "reason": n.get("reason"),
        "unread": n.get("unread"),
        "updated_at": n.get("updated_at"),
        "repo": (n.get("repository") or {}).get("full_name"),
        "type": subject.get("type"),
        "title": subject.get("title"),
        # subject.url is an API URL the agent can follow for details; keep it
        # so tools can chain (e.g. fetch the PR/issue it points at).
        "subject_api_url": subject.get("url"),
    }


# ── Tools (read-only) ──

TOOLS: list[Tool] = [
    Tool(
        name="gh_me",
        description="Get the authenticated GitHub user's profile. Use to confirm the integration is live and which account is connected.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="gh_list_my_prs",
        description=(
            "List pull requests authored by or assigned to the authenticated user across all "
            "repos. Use as the entry point when the user asks about their own open PRs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                "involves_me": {
                    "type": "string",
                    "enum": ["author", "assignee", "mentioned", "review-requested", "involves"],
                    "default": "involves",
                    "description": "How the user is associated. 'involves' is the broadest.",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="gh_get_pr",
        description="Get full details of one pull request including title, body, branch info, commit/file counts, and labels. Does NOT include the diff or comments — use gh_get_pr_diff and gh_list_pr_comments for those.",
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repo owner (user or org)."},
                "repo": {"type": "string", "description": "Repo name."},
                "number": {"type": "integer", "description": "PR number."},
            },
            "required": ["owner", "repo", "number"],
        },
    ),
    Tool(
        name="gh_get_pr_diff",
        description="Get the unified diff for a PR as raw text. Use this when the user asks about what changed, code review, or to understand the scope of a PR. Large PRs may be truncated — pass max_chars to control.",
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "max_chars": {"type": "integer", "default": 30000, "description": "Truncate the diff body to this many chars."},
            },
            "required": ["owner", "repo", "number"],
        },
    ),
    Tool(
        name="gh_list_pr_comments",
        description="List both issue-style comments and inline review comments on a PR, merged together and ordered by creation time. Use when responding to maintainer feedback.",
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "number": {"type": "integer"},
            },
            "required": ["owner", "repo", "number"],
        },
    ),
    Tool(
        name="gh_search_issues",
        description=(
            "Search GitHub issues and PRs across all of GitHub using the standard search "
            "syntax (e.g. 'repo:owner/name is:pr state:open author:me'). Use to find related "
            "or duplicate work before drafting."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Standard GitHub search query."},
                "per_page": {"type": "integer", "default": 10, "maximum": 30},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="gh_get_notifications",
        description=(
            "List the authenticated user's GitHub notifications (unread by default). Use as a "
            "starting point when the user asks 'what's new' or 'anything need my attention', or "
            "when surfacing the unread count the user already knows about."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "default": False, "description": "Include read notifications too."},
                "per_page": {"type": "integer", "default": 20, "maximum": 50},
            },
            "required": [],
        },
    ),

    # ── WRITE TOOLS ──
    # Gated upstream by `write_enabled` in chat_routes.py:_gh_write_tools — these
    # are unreachable unless the user has explicitly enabled write actions in
    # Settings. Per the briefing's WRITE ACTIONS section the agent should say
    # WHAT + WHY before invoking one and WHAT + a link after.

    Tool(
        name="gh_post_pr_comment",
        description=(
            "Post a top-level (issue-style) comment on a pull request. NOT for inline review "
            "comments on specific diff lines. Use this for general PR feedback or replies in the "
            "main thread."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repo owner (user or org)."},
                "repo": {"type": "string", "description": "Repo name."},
                "number": {"type": "integer", "description": "PR number."},
                "body": {"type": "string", "description": "Comment body. Supports GitHub-flavored markdown."},
            },
            "required": ["owner", "repo", "number", "body"],
        },
    ),
    Tool(
        name="gh_post_issue_comment",
        description="Post a comment on an issue. Same endpoint as PR comments under the hood (GitHub treats PRs as issues), but use this name when the target is an actual issue for clarity in chat logs.",
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "number": {"type": "integer", "description": "Issue number."},
                "body": {"type": "string"},
            },
            "required": ["owner", "repo", "number", "body"],
        },
    ),
    Tool(
        name="gh_open_pr",
        description=(
            "Open a new pull request. Requires the head branch to already exist on the remote "
            "(use gh_push_commits first if needed). Body is optional. The PR's title, body, and "
            "structure are the user's call per the briefing — don't impose boilerplate templates "
            "or your own conventions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repo owner of the BASE repo."},
                "repo": {"type": "string", "description": "Repo name of the BASE repo."},
                "title": {"type": "string"},
                "head": {"type": "string", "description": "Source branch. For a fork, use 'forkowner:branch'."},
                "base": {"type": "string", "description": "Target branch (e.g. 'main')."},
                "body": {"type": "string", "description": "Optional PR body. Markdown."},
                "draft": {"type": "boolean", "default": False, "description": "Open as a draft PR."},
            },
            "required": ["owner", "repo", "title", "head", "base"],
        },
    ),
    Tool(
        name="gh_edit_pr",
        description=(
            "Edit an existing pull request — change title, body, base branch, or state. Reversible. "
            "Pass only the fields you want to change; omitted fields stay as-is."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed"]},
                "base": {"type": "string", "description": "Rebase target branch."},
            },
            "required": ["owner", "repo", "number"],
        },
    ),
    Tool(
        name="gh_close_issue",
        description=(
            "Close an issue. Reversible (can be reopened). For PRs, use gh_edit_pr with state:closed "
            "instead — that preserves PR-specific metadata. state_reason 'completed' means resolved; "
            "'not_planned' means won't-fix / out-of-scope."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "number": {"type": "integer", "description": "Issue number."},
                "state_reason": {
                    "type": "string",
                    "enum": ["completed", "not_planned"],
                    "default": "completed",
                },
            },
            "required": ["owner", "repo", "number"],
        },
    ),
    Tool(
        name="gh_mark_notification_read",
        description=(
            "Mark a single notification thread as read by ID, or all of the user's notifications "
            "as read (when 'all' is true and thread_id is omitted). Threads with new activity will "
            "re-appear as unread later."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Specific thread ID from gh_get_notifications. Omit when 'all' is true."},
                "all": {"type": "boolean", "default": False, "description": "Mark every unread notification as read. Mutually exclusive with thread_id."},
            },
            "required": [],
        },
    ),
    Tool(
        name="gh_push_commits",
        description=(
            "Push a local git working tree's branch to its remote, using the user's GitHub PAT for "
            "HTTPS authentication. Assumes commits have already been authored locally (this tool does "
            "NOT create commits). Requires repo_path to be a real git working tree on the Odysseus "
            "host, so it only works for local/self-hosted installs where Odysseus runs alongside "
            "the user's clones (not Docker or cloud-hosted deployments without volume mounts). "
            "When force=true, uses --force-with-lease (refuses if the remote ref advanced since you "
            "last fetched). "
            "ON LEASE REJECTION (`(stale info)` in output): do NOT silently retry with force=true. "
            "Fetch the remote, inspect what changed (git log origin/<branch>..HEAD and the reverse), "
            "then decide. If the remote changes are real conflicting work, tell the user before doing "
            "anything destructive. "
            "ON SECRET SCAN BLOCK (response has `blocked: 'secret_scan'`): the diff contains likely "
            "credentials. Do NOT try to bypass — there's no bypass flag. Tell the user which "
            "files/lines flagged, that the gate is intentional, and that they should remove the "
            "content from the diff before retrying. If they insist it's a false positive, they can "
            "run `git push` themselves — but make them confirm it explicitly first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Absolute filesystem path to the local git working tree."},
                "branch": {"type": "string", "description": "Branch to push. Defaults to the current branch in the working tree."},
                "remote": {"type": "string", "default": "origin"},
                "force": {"type": "boolean", "default": False, "description": "Use --force-with-lease. Required for rebased branches; otherwise GitHub will reject."},
                "set_upstream": {"type": "boolean", "default": True, "description": "Pass -u so the local branch tracks the remote (no-op on already-tracked branches)."},
            },
            "required": ["repo_path"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    args = dict(arguments or {})
    # Per-call identity. The trusted dispatch layer
    # (src/tool_execution.execute_tool_block) stamps the authenticated Odysseus
    # owner onto every github tool call as `_owner`. Pop it BEFORE dispatch so
    # (a) it can never leak into a GitHub API request and (b) this call's PAT
    # is looked up by it. If `_owner` is absent — a non-agent caller that
    # didn't stamp it — fall back to single-owner DB resolution, which fails
    # CLOSED on ambiguity (raises AmbiguousOwnerError) rather than guessing.
    owner = args.pop("_owner", _OWNER_UNSET)
    try:
        if owner is _OWNER_UNSET:
            owner = _resolve_owner()  # may raise AmbiguousOwnerError
        result = await _dispatch(name, args, owner)
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    if isinstance(result, str):
        return [TextContent(type="text", text=result)]
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _dispatch(name: str, args: dict, acting_owner: str | None) -> Any:
    # `acting_owner` is the Odysseus identity for this call. It is deliberately
    # NOT named `owner`: several branches below rebind a local `owner` to the
    # GitHub *repo* owner from args, and the `gh` closure must keep routing the
    # PAT by the Odysseus user, not whatever repo is being addressed.
    async def gh(method, path, **kw):
        return await _gh_request(method, path, owner=acting_owner, **kw)

    if name == "gh_me":
        me = await gh("GET", "/user")
        if not me:
            return {"error": "no response"}
        return {
            "login": me.get("login"),
            "name": me.get("name"),
            "html_url": me.get("html_url"),
            "public_repos": me.get("public_repos"),
            "followers": me.get("followers"),
        }

    if name == "gh_list_my_prs":
        state = args.get("state", "open")
        involves = args.get("involves_me", "involves")
        me = await gh("GET", "/user")
        login = (me or {}).get("login") if me else None
        if not login:
            return {"error": "could not resolve current user"}
        # GitHub search supports state:open is:pr involves:LOGIN
        state_part = "" if state == "all" else f"state:{state}"
        query = f"is:pr {state_part} {involves}:{login}".strip()
        out = await gh("GET", "/search/issues", params={"q": query, "per_page": 30, "sort": "updated"})
        items = (out or {}).get("items", [])
        return {"count": (out or {}).get("total_count", 0), "items": [_trim_issue(i) for i in items]}

    if name == "gh_get_pr":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        pr = await gh("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return _trim_pr(pr or {})

    if name == "gh_get_pr_diff":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        max_chars = int(args.get("max_chars") or 30000)
        # Request the diff representation directly via the Accept header trick.
        # Our _gh_request hardcodes a JSON Accept header; for the diff we hit
        # httpx directly with an override — but route the PAT by THIS call's
        # owner, same as every other request.
        if acting_owner is None:
            raise RuntimeError("No GitHub integration owner found — set one up in Settings → Integrations.")
        pat = _load_pat(acting_owner)
        if not pat:
            raise RuntimeError(f"No GitHub PAT found for user '{acting_owner}' (integration disabled or missing).")
        url = f"{GH_API}/repos/{owner}/{repo}/pulls/{number}"
        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.v3.diff",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        async with httpx.AsyncClient(timeout=GH_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub API {resp.status_code}: {resp.text[:200]}")
        diff = resp.text or ""
        truncated = len(diff) > max_chars
        body = diff[:max_chars] + ("\n... [truncated]" if truncated else "")
        return {"truncated": truncated, "diff": body, "total_chars": len(diff)}

    if name == "gh_list_pr_comments":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        # PRs have two comment streams: issue comments on the PR itself,
        # and review/inline comments on the diff. Merge into one timeline.
        issue_comments = await gh("GET", f"/repos/{owner}/{repo}/issues/{number}/comments", params={"per_page": 50})
        review_comments = await gh("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments", params={"per_page": 50})
        merged: list = []
        for c in (issue_comments or []):
            merged.append({**_trim_comment(c), "_stream": "issue"})
        for c in (review_comments or []):
            merged.append({**_trim_comment(c), "_stream": "review"})
        merged.sort(key=lambda c: c.get("created_at") or "")
        return {"count": len(merged), "comments": merged}

    if name == "gh_search_issues":
        query = args["query"]
        per_page = min(int(args.get("per_page") or 10), 30)
        out = await gh("GET", "/search/issues", params={"q": query, "per_page": per_page, "sort": "updated"})
        items = (out or {}).get("items", [])
        return {"total": (out or {}).get("total_count", 0), "items": [_trim_issue(i) for i in items]}

    if name == "gh_get_notifications":
        params: dict = {
            "all": "true" if args.get("all") else "false",
            "per_page": min(int(args.get("per_page") or 20), 50),
        }
        out = await gh("GET", "/notifications", params=params)
        return {"count": len(out or []), "notifications": [_trim_notification(n) for n in (out or [])]}

    # ── WRITE TOOLS ──

    if name == "gh_post_pr_comment" or name == "gh_post_issue_comment":
        # Same endpoint either way — GitHub treats PRs as issues for the
        # general comment thread. The split tool names are purely for clarity.
        owner, repo, number = args["owner"], args["repo"], args["number"]
        body = args["body"]
        out = await gh(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json_body={"body": body},
        )
        return {
            "id": (out or {}).get("id"),
            "url": (out or {}).get("html_url"),
            "created_at": (out or {}).get("created_at"),
        }

    if name == "gh_open_pr":
        owner, repo = args["owner"], args["repo"]
        body = {
            "title": args["title"],
            "head": args["head"],
            "base": args["base"],
        }
        # Optional fields — only include if the agent passed them, so we
        # don't clobber GitHub defaults with empty strings.
        if "body" in args and args["body"] is not None:
            body["body"] = args["body"]
        if args.get("draft"):
            body["draft"] = True
        out = await gh("POST", f"/repos/{owner}/{repo}/pulls", json_body=body)
        return _trim_pr(out or {})

    if name == "gh_edit_pr":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        # Partial PATCH body — only fields the agent explicitly specified.
        body: dict = {}
        for f in ("title", "body", "state", "base"):
            if f in args and args[f] is not None:
                body[f] = args[f]
        if not body:
            return {"error": "Nothing to update — pass at least one of: title, body, state, base."}
        out = await gh("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", json_body=body)
        return _trim_pr(out or {})

    if name == "gh_close_issue":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        reason = args.get("state_reason") or "completed"
        out = await gh(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{number}",
            json_body={"state": "closed", "state_reason": reason},
        )
        return {
            "number": (out or {}).get("number"),
            "state": (out or {}).get("state"),
            "state_reason": (out or {}).get("state_reason"),
            "url": (out or {}).get("html_url"),
        }

    if name == "gh_mark_notification_read":
        thread_id = args.get("thread_id")
        if thread_id:
            await gh("PATCH", f"/notifications/threads/{thread_id}")
            return {"marked": "one", "thread_id": thread_id}
        if args.get("all"):
            # Bulk mark-all-read: PUT /notifications (last_read_at defaults to now).
            await gh("PUT", "/notifications", json_body={})
            return {"marked": "all"}
        return {"error": "Pass thread_id (single) or all=true (bulk)."}

    if name == "gh_push_commits":
        return await _git_push_subprocess(args, acting_owner)

    raise RuntimeError(f"Unknown tool: {name}")


# ── Pre-push secret scan ──
# Hard gate against the single most catastrophic mistake an agent can make on
# the user's behalf: pushing a commit that contains credentials. Once a secret
# reaches a shared remote it's burned — rotation is the only fix, and the
# original commit lives forever in history, clone caches, and mirrors. So this
# scan runs on the diff that's about to leave the local machine, BEFORE the push.
#
# Threat model: an agent that knows what it's doing but can still slip (commits
# a .env, leaves a debug-printed token in a fixture). NOT a malicious agent
# trying to bypass — those can run git push directly via a shell. This saves the
# user from their assistant's accidental honesty, it doesn't sandbox it.
#
# Fail-open on scan errors: if the scan crashes or git refuses to diff, we
# proceed with the push. Blocking every push on a scanner bug is worse UX than
# the original problem.

# High-confidence credential formats only (low false-positive rate). Generic
# high-entropy detection is deliberately excluded — too many legit hits in test
# fixtures, hashes, base64 assets. Better to miss a bespoke secret than cry wolf.
_SECRET_PATTERNS = {
    "AWS Access Key ID": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub Classic PAT": re.compile(r"ghp_[0-9A-Za-z]{36}"),
    "GitHub Fine-grained PAT": re.compile(r"github_pat_[0-9A-Za-z_]{82}"),
    "GitHub OAuth Token": re.compile(r"gho_[0-9A-Za-z]{36}"),
    "OpenAI API Key (legacy)": re.compile(r"sk-[A-Za-z0-9]{48}(?![A-Za-z0-9])"),
    "OpenAI API Key (project)": re.compile(r"sk-proj-[A-Za-z0-9_-]{40,}"),
    "Anthropic API Key": re.compile(r"sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{80,}"),
    "Google API Key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "Stripe Live Secret Key": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
    "Slack Token": re.compile(r"xox[baprs]-[0-9a-zA-Z-]{10,}"),
    "Private Key Block (PEM)": re.compile(r"-----BEGIN[ A-Z]+PRIVATE KEY-----"),
    "JWT-like Token": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+"),
}

# Filenames that almost always contain secrets when committed. We flag any diff
# that ADDS one (deleting a .env is the safe direction). Basename match,
# case-insensitive.
_SENSITIVE_FILENAME_PATTERNS = [
    re.compile(r"^\.env(\..+)?$", re.I),         # .env, .env.local, .env.production
    re.compile(r".*credentials.*\.(json|yaml|yml|txt)$", re.I),
    re.compile(r".*secrets?.*\.(json|yaml|yml|txt)$", re.I),
    re.compile(r".*\.pem$", re.I),
    re.compile(r".*\.key$", re.I),
    re.compile(r"id_rsa$|id_ed25519$|id_ecdsa$|id_dsa$", re.I),  # SSH private keys
    re.compile(r".*\.pfx$|.*\.p12$", re.I),       # PKCS#12 bundles
]


def _scan_diff_for_secret_patterns(diff_text: str) -> list[dict]:
    """Walk a unified diff and report regex matches on ADDED lines only.
    Removing a leaked secret is cleanup, not a leak — only additions matter."""
    findings: list[dict] = []
    current_file = None
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if not raw.startswith("+"):
            continue
        content = raw[1:]
        for label, pattern in _SECRET_PATTERNS.items():
            m = pattern.search(content)
            if m:
                # Trim + mask so we don't dump the whole secret into agent
                # context (which goes to logs, and logs leak).
                snippet = content.strip()
                if len(snippet) > 120:
                    snippet = snippet[:60] + "..." + snippet[-30:]
                secret_text = m.group(0)
                if len(secret_text) > 12:
                    masked = secret_text[:4] + "*" * (len(secret_text) - 8) + secret_text[-4:]
                else:
                    masked = "*" * len(secret_text)
                snippet = snippet.replace(secret_text, masked)
                findings.append({
                    "type": label,
                    "file": current_file or "<unknown>",
                    "snippet": snippet,
                })
    return findings


def _scan_diff_for_sensitive_filenames(diff_text: str) -> list[dict]:
    """Report newly-added files whose names match the sensitive-filename
    patterns. New file is detected via git's '--- /dev/null' marker."""
    findings: list[dict] = []
    sections = re.split(r"^diff --git ", diff_text, flags=re.MULTILINE)
    for section in sections[1:]:
        header_line = section.split("\n", 1)[0]
        m = re.match(r"a/(\S+) b/(\S+)", header_line)
        if not m:
            continue
        path_b = m.group(2)
        basename = path_b.rsplit("/", 1)[-1]
        if "--- /dev/null" not in section:
            continue
        for pat in _SENSITIVE_FILENAME_PATTERNS:
            if pat.match(basename):
                findings.append({
                    "type": "Sensitive filename",
                    "file": path_b,
                    "snippet": f"new file: {basename}",
                })
                break
    return findings


async def _pre_push_secret_scan(
    repo_dir: Path, remote: str, branch: str, env: dict,
) -> list[dict]:
    """Diff what's about to be pushed and scan it for secrets.

    Precise diff: remote tracking ref..HEAD. If the remote branch doesn't exist
    yet (first push), fall back to the local branch's last 20 commits. Fail-open
    on any error (returns []) so a scan bug never blocks a legitimate push."""
    try:
        # Scan the commits that THIS push will publish: local <branch> beyond
        # the remote tracking ref. Use the named branch (not HEAD) so the scan
        # matches the ref being pushed even when the caller passes a branch
        # other than the checked-out one.
        cmd = ["git", "-C", str(repo_dir), "log", "-p", "--no-color",
               f"{remote}/{branch}..{branch}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            cmd_fallback = ["git", "-C", str(repo_dir), "log", "-p", "--no-color",
                            "--max-count=20", branch]
            proc = subprocess.run(cmd_fallback, capture_output=True, text=True,
                                  env=env, timeout=30)
            if proc.returncode != 0:
                return []
        diff = proc.stdout or ""
        findings = _scan_diff_for_secret_patterns(diff)
        findings.extend(_scan_diff_for_sensitive_filenames(diff))
        return findings
    except Exception:
        return []  # fail-open


async def _git_push_subprocess(args: dict, owner: str | None) -> dict:
    """Push a local branch using `owner`'s PAT as the HTTPS credential.

    The PAT (when present) is injected into the subprocess env and consumed by
    a one-shot credential helper below; SSH remotes ignore it and use the agent
    (orthogonal). `owner` is the per-call identity resolved by the dispatcher.
    Returns a structured result so the agent can confirm success and surface
    failure output verbatim."""
    repo_path = args.get("repo_path") or ""
    if not repo_path:
        return {"error": "repo_path is required."}
    repo_dir = Path(repo_path).expanduser().resolve()
    if not repo_dir.exists():
        return {"error": f"Path does not exist: {repo_dir}"}
    if not (repo_dir / ".git").exists():
        return {"error": f"Not a git working tree (no .git): {repo_dir}"}

    # owner == "" is a valid lookup key (solo install); only None means "none".
    pat = _load_pat(owner) if owner is not None else None
    # PAT not hard-required — SSH remotes work without one. Inject if present.
    env = os.environ.copy()
    if pat:
        env["GH_TOKEN"] = pat
        env["GITHUB_TOKEN"] = pat

    remote = args.get("remote") or "origin"
    branch = args.get("branch") or None  # None → resolved from working tree
    set_upstream = args.get("set_upstream", True)
    force = bool(args.get("force"))

    if not branch:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            branch = (proc.stdout or "").strip()
            if not branch or branch == "HEAD":
                return {"error": "Detached HEAD or could not resolve current branch — pass `branch` explicitly."}
        except Exception as e:
            return {"error": f"Failed to read current branch: {e}"}

    # ── HARD GATE: pre-push secret scan ──
    # Non-overridable from the tool side (no bypass flag) — an agent passing
    # bypass=true would defeat the point. A user who truly needs to override
    # can run `git push` themselves, which is a deliberate human action.
    findings = await _pre_push_secret_scan(repo_dir, remote, branch, env)
    if findings:
        by_type: dict[str, list[dict]] = {}
        for f in findings:
            by_type.setdefault(f["type"], []).append(f)
        return {
            "ok": False,
            "blocked": "secret_scan",
            "error": (
                "Refusing to push: the diff contains content that looks like "
                "credentials or sensitive files. Once pushed, secrets are "
                "effectively permanent (history, mirrors, clone caches) — "
                "rotating them is the only fix. Remove the offending content "
                "from the diff (use `git reset --soft HEAD~N` to unstage, edit, "
                "re-commit) and try again."
            ),
            "findings": [
                {
                    "type": t,
                    "occurrences": len(items),
                    "files": list({i["file"] for i in items}),
                    "first_match_snippet": items[0]["snippet"],
                }
                for t, items in by_type.items()
            ],
            "bypass_note": (
                "There is no bypass flag on this tool. If you are confident this "
                "is a false positive (a redacted example, a test fixture, a "
                "public demo token), you can run `git push` yourself — that "
                "requires deliberate human action, which is the point."
            ),
        }

    cmd = ["git", "-C", str(repo_dir)]
    if pat:
        # Plain `git` does NOT consume GH_TOKEN/GITHUB_TOKEN for HTTPS auth —
        # those are gh-CLI vars. So for an HTTPS GitHub remote we install a
        # one-shot credential helper that feeds the token (read from the env we
        # set above, never from argv, so it stays out of the process list).
        # We blank any inherited helper first so a misconfigured global helper
        # can't shadow ours. SSH remotes ignore credential helpers entirely, so
        # this is a no-op there.
        cmd += [
            "-c", "credential.helper=",
            "-c", 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f',
        ]
    cmd.append("push")
    if set_upstream:
        cmd.append("-u")
    if force:
        # Always --force-with-lease, never raw --force.
        cmd.append("--force-with-lease")
    cmd.extend([remote, branch])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"error": "git push timed out after 120s."}
    except Exception as e:
        return {"error": f"git push subprocess failed to start: {e}"}

    # Git prints success info to stderr (remote: lines + the branch summary).
    combined = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "branch": branch,
        "remote": remote,
        "force_with_lease": force,
        "output": combined.strip(),
    }


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
