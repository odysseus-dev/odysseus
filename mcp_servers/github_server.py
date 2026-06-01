"""
github_server.py

MCP server exposing GitHub API tools (repos, issues, PRs, search, notifications).
Uses httpx to call GitHub REST API v3. Auth via GITHUB_TOKEN env var or
integration-stored token.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("github")

_http_client = None
_initialized = False

GITHUB_API = "https://api.github.com"


def _ensure_init():
    """Lazy-init httpx client on first use."""
    global _http_client, _initialized
    if _initialized:
        return
    _initialized = True
    import httpx
    _http_client = httpx.AsyncClient(
        base_url=GITHUB_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )


def _get_token() -> str:
    """Get GitHub token from env or integration config."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    # Try loading from integrations.json
    try:
        data_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "integrations.json",
        )
        if os.path.exists(data_file):
            with open(data_file) as f:
                for integ in json.load(f):
                    if integ.get("preset") == "github" and integ.get("api_key"):
                        return integ["api_key"]
    except Exception:
        pass
    return ""


def _auth_headers() -> dict:
    token = _get_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="manage_github",
            description=(
                "Interact with GitHub: list repos, view issues/PRs, search code, check notifications. "
                "Requires GITHUB_TOKEN env var or a GitHub integration configured in Settings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_repos", "get_repo", "list_issues", "get_issue",
                            "list_prs", "get_pr", "search_code", "list_notifications",
                            "list_commits", "get_readme",
                        ],
                        "description": "The action to perform",
                    },
                    "owner": {"type": "string", "description": "Repository owner (username or org)"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "query": {"type": "string", "description": "Search query (for search_code)"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Filter state (issues/PRs)"},
                    "number": {"type": "integer", "description": "Issue or PR number"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                    "page": {"type": "integer", "description": "Page number (default 1)"},
                },
                "required": ["action"],
            },
        )
    ]


async def _gh_get(path: str, params: dict = None) -> dict:
    """Make a GET request to GitHub API."""
    headers = _auth_headers()
    resp = await _http_client.get(path, params=params, headers=headers)
    if resp.status_code == 401:
        return {"error": "GitHub authentication failed. Set GITHUB_TOKEN or configure a GitHub integration."}
    if resp.status_code == 403:
        return {"error": f"GitHub API rate limit or permission error: {resp.text[:200]}"}
    if resp.status_code == 404:
        return {"error": f"Not found: {path}"}
    if resp.status_code >= 400:
        return {"error": f"GitHub API error {resp.status_code}: {resp.text[:200]}"}
    return resp.json()


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "manage_github":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    _ensure_init()
    action = arguments.get("action", "")
    owner = arguments.get("owner", "")
    repo = arguments.get("repo", "")
    limit = arguments.get("limit", 20)
    page = arguments.get("page", 1)

    try:
        if action == "list_repos":
            data = await _gh_get("/user/repos", {"per_page": limit, "page": page, "sort": "updated"})
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            lines = [f"Found {len(data)} repositories:\n"]
            for r in data:
                stars = r.get("stargazers_count", 0)
                lang = r.get("language") or "?"
                desc = (r.get("description") or "")[:80]
                lines.append(f"- **{r['full_name']}** ⭐{stars} [{lang}] — {desc}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "get_repo":
            if not owner or not repo:
                return [TextContent(type="text", text="Error: owner and repo are required")]
            data = await _gh_get(f"/repos/{owner}/{repo}")
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            info = (
                f"**{data['full_name']}** — {data.get('description', 'No description')}\n"
                f"Stars: {data.get('stargazers_count', 0)} | Forks: {data.get('forks_count', 0)} | "
                f"Open issues: {data.get('open_issues_count', 0)}\n"
                f"Language: {data.get('language', 'N/A')} | "
                f"Default branch: {data.get('default_branch', 'main')}\n"
                f"URL: {data.get('html_url', '')}\n"
                f"Created: {data.get('created_at', '')} | Updated: {data.get('updated_at', '')}"
            )
            return [TextContent(type="text", text=info)]

        elif action == "list_issues":
            if not owner or not repo:
                return [TextContent(type="text", text="Error: owner and repo are required")]
            params = {"per_page": limit, "page": page}
            if arguments.get("state"):
                params["state"] = arguments["state"]
            data = await _gh_get(f"/repos/{owner}/{repo}/issues", params)
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            lines = [f"Issues for {owner}/{repo} ({len(data)} shown):\n"]
            for issue in data:
                if "pull_request" in issue:
                    continue  # skip PRs in issue list
                labels = ", ".join(l["name"] for l in issue.get("labels", []))
                label_str = f" [{labels}]" if labels else ""
                lines.append(f"- #{issue['number']} {issue['title']}{label_str} — {issue['state']}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "get_issue":
            if not owner or not repo or not arguments.get("number"):
                return [TextContent(type="text", text="Error: owner, repo, and number are required")]
            num = arguments["number"]
            data = await _gh_get(f"/repos/{owner}/{repo}/issues/{num}")
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            labels = ", ".join(l["name"] for l in data.get("labels", []))
            body = (data.get("body") or "")[:500]
            info = (
                f"#{data['number']} — {data['title']}\n"
                f"State: {data['state']} | Author: {data['user']['login']} | Labels: {labels or 'none'}\n"
                f"Created: {data.get('created_at', '')} | Comments: {data.get('comments', 0)}\n\n"
                f"{body}"
            )
            return [TextContent(type="text", text=_truncate(info))]

        elif action == "list_prs":
            if not owner or not repo:
                return [TextContent(type="text", text="Error: owner and repo are required")]
            params = {"per_page": limit, "page": page}
            if arguments.get("state"):
                params["state"] = arguments["state"]
            data = await _gh_get(f"/repos/{owner}/{repo}/pulls", params)
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            lines = [f"Pull requests for {owner}/{repo} ({len(data)} shown):\n"]
            for pr in data:
                lines.append(f"- #{pr['number']} {pr['title']} — {pr['state']} by {pr['user']['login']}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "get_pr":
            if not owner or not repo or not arguments.get("number"):
                return [TextContent(type="text", text="Error: owner, repo, and number are required")]
            num = arguments["number"]
            data = await _gh_get(f"/repos/{owner}/{repo}/pulls/{num}")
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            body = (data.get("body") or "")[:500]
            info = (
                f"PR #{data['number']} — {data['title']}\n"
                f"State: {data['state']} | Author: {data['user']['login']}\n"
                f"Base: {data.get('base', {}).get('ref', '?')} ← Head: {data.get('head', {}).get('ref', '?')}\n"
                f"Mergeable: {data.get('mergeable', '?')} | Merge conflicts: {data.get('mergeable_state', '?')}\n"
                f"Commits: {data.get('commits', 0)} | Changed files: {data.get('changed_files', 0)}\n"
                f"+{data.get('additions', 0)} -{data.get('deletions', 0)}\n\n"
                f"{body}"
            )
            return [TextContent(type="text", text=_truncate(info))]

        elif action == "search_code":
            query = arguments.get("query", "")
            if not query:
                return [TextContent(type="text", text="Error: query is required")]
            data = await _gh_get("/search/code", {"q": query, "per_page": limit, "page": page})
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            items = data.get("items", [])
            total = data.get("total_count", 0)
            lines = [f"Code search: {total} results for '{query}' (showing {len(items)}):\n"]
            for item in items:
                repo_name = item.get("repository", {}).get("full_name", "?")
                lines.append(f"- {item['path']} in {repo_name}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "list_notifications":
            params = {"per_page": limit, "page": page}
            if arguments.get("state"):
                params["all"] = "true" if arguments["state"] == "all" else "false"
            data = await _gh_get("/notifications", params)
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            if not data:
                return [TextContent(type="text", text="No notifications found.")]
            lines = [f"GitHub notifications ({len(data)}):\n"]
            for n in data[:limit]:
                repo = n.get("repository", {}).get("full_name", "?")
                subject = n.get("subject", {})
                lines.append(f"- [{subject.get('type', '?')}] {subject.get('title', '?')} in {repo}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "list_commits":
            if not owner or not repo:
                return [TextContent(type="text", text="Error: owner and repo are required")]
            params = {"per_page": limit, "page": page}
            data = await _gh_get(f"/repos/{owner}/{repo}/commits", params)
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            lines = [f"Recent commits for {owner}/{repo} ({len(data)}):\n"]
            for c in data:
                sha = c["sha"][:7]
                msg = c["commit"]["message"].split("\n")[0][:80]
                author = c["commit"]["author"]["name"]
                date = c["commit"]["author"]["date"][:10]
                lines.append(f"- `{sha}` {msg} — {author} ({date})")
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "get_readme":
            if not owner or not repo:
                return [TextContent(type="text", text="Error: owner and repo are required")]
            data = await _gh_get(f"/repos/{owner}/{repo}/readme")
            if "error" in data:
                return [TextContent(type="text", text=data["error"])]
            import base64
            content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            return [TextContent(type="text", text=_truncate(content))]

        else:
            return [TextContent(type="text", text=f"Error: Unknown action '{action}'. Use: list_repos, get_repo, list_issues, get_issue, list_prs, get_pr, search_code, list_notifications, list_commits, get_readme")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
