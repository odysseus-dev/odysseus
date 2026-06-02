"""
github_server.py

MCP server exposing read-only GitHub tools — list the user's PRs, fetch a
PR's details/diff/comments, and search issues. Authenticated with a per-user
Personal Access Token stored encrypted in `github_integrations.pat_encrypted`
and decrypted at request time.

Routing: a request comes in with no per-user context (MCP is process-wide).
We resolve which user's PAT to use via the `ODYSSEUS_GH_OWNER` env var that the
spawner injects when the server is started. For now we run one MCP instance per
app process, scoped to whichever owner the integration row belongs to.
Multi-tenant servers can later switch this to per-call routing.

Tool gating happens upstream in routes/chat_routes.py: the gh_* family is added
to disabled_tools unless the user has the integration enabled AND toggled it on
for the turn. These tools are deliberately minimal wrappers around the GitHub
REST API; the trimming helpers keep response bodies small so we don't shove
hundreds of KB of JSON into the agent's context on every call.
"""

import json
import os
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


def _resolve_owner() -> str | None:
    """Owner the MCP server is scoped to. Injected by builtin_mcp's spawner
    via the ODYSSEUS_GH_OWNER env var when the per-user integration is
    registered. Falls back to a default for solo-user installs."""
    env_owner = os.environ.get("ODYSSEUS_GH_OWNER")
    if env_owner:
        return env_owner
    # Single-user fallback: return the first integration row's owner, if any.
    path = _db_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        row = conn.execute(
            "SELECT owner FROM github_integrations WHERE enabled = 1 LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


async def _gh_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict | list | None:
    """Hit the GitHub REST API with the current owner's PAT.

    Returns parsed JSON. On HTTP errors raises with the upstream message so
    the agent can surface useful failure context. Network errors are also
    raised; callers in the tool layer should catch and return error
    TextContent so the agent doesn't crash."""
    owner = _resolve_owner()
    if not owner:
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
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments or {})
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    if isinstance(result, str):
        return [TextContent(type="text", text=result)]
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _dispatch(name: str, args: dict) -> Any:
    if name == "gh_me":
        me = await _gh_request("GET", "/user")
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
        me = await _gh_request("GET", "/user")
        login = (me or {}).get("login") if me else None
        if not login:
            return {"error": "could not resolve current user"}
        # GitHub search supports state:open is:pr involves:LOGIN
        state_part = "" if state == "all" else f"state:{state}"
        query = f"is:pr {state_part} {involves}:{login}".strip()
        out = await _gh_request("GET", "/search/issues", params={"q": query, "per_page": 30, "sort": "updated"})
        items = (out or {}).get("items", [])
        return {"count": (out or {}).get("total_count", 0), "items": [_trim_issue(i) for i in items]}

    if name == "gh_get_pr":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        pr = await _gh_request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return _trim_pr(pr or {})

    if name == "gh_get_pr_diff":
        owner, repo, number = args["owner"], args["repo"], args["number"]
        max_chars = int(args.get("max_chars") or 30000)
        # Request the diff representation directly via the Accept header trick.
        # Our _gh_request hardcodes a JSON Accept header; for the diff we hit
        # httpx directly with an override.
        pat = _load_pat(_resolve_owner() or "")
        if not pat:
            raise RuntimeError("No GitHub PAT configured.")
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
        issue_comments = await _gh_request("GET", f"/repos/{owner}/{repo}/issues/{number}/comments", params={"per_page": 50})
        review_comments = await _gh_request("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments", params={"per_page": 50})
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
        out = await _gh_request("GET", "/search/issues", params={"q": query, "per_page": per_page, "sort": "updated"})
        items = (out or {}).get("items", [])
        return {"total": (out or {}).get("total_count", 0), "items": [_trim_issue(i) for i in items]}

    if name == "gh_get_notifications":
        params: dict = {
            "all": "true" if args.get("all") else "false",
            "per_page": min(int(args.get("per_page") or 20), 50),
        }
        out = await _gh_request("GET", "/notifications", params=params)
        return {"count": len(out or []), "notifications": [_trim_notification(n) for n in (out or [])]}

    raise RuntimeError(f"Unknown tool: {name}")


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
