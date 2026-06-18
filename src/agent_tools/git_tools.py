"""Workspace-confined `git` and `forge` (gh/glab) agent tools (TOOL_HANDLERS).

push/fetch/pull use a strict default-deny allowlist: rejected unless they match
an explicitly safe shape, so force/mirror/delete/+refspec/URL/--upload-pack
forms fail by construction rather than by enumerating bad flags.
"""
import asyncio
import os
import re
import shlex
import shutil
import subprocess
from typing import Optional

from src.constants import MAX_OUTPUT_CHARS

# ── git policy ──────────────────────────────────────────────────────────────
_GIT_ALLOWED = frozenset({
    # read
    "status", "diff", "log", "show", "branch", "blame", "ls-files",
    "rev-parse", "shortlog", "describe", "tag", "remote", "stash",
    # local write
    "add", "commit", "restore", "checkout", "switch", "reset", "rm", "mv",
    "merge", "rebase", "cherry-pick", "revert", "init",
    # network (narrow, validated below)
    "push", "fetch", "pull",
})
_GIT_BLOCKED = frozenset({
    "config", "clone", "daemon", "gc", "submodule", "credential",
    "remote-add", "filter-branch", "update-ref", "fast-import",
})
# Path-redirecting global options that would escape the workspace.
_GIT_BANNED_ARGS = frozenset({"-C", "--git-dir", "--work-tree", "--exec-path"})
# Flags accepted on fetch/pull. Anything else (incl. --upload-pack=...,
# --receive-pack, --exec, -c) is rejected.
_FETCH_PULL_SAFE_FLAGS = frozenset({
    "--ff-only", "--prune", "-p", "--tags", "--no-tags", "-t", "--all",
})
_GIT_TIMEOUT = 60

# ── forge policy (gh / glab) ─────────────────────────────────────────────────
_FORGE_ALLOWED = frozenset({
    "pr", "mr", "issue", "repo", "release", "label", "milestone",
})
_FORGE_BLOCKED_SUBVERBS = frozenset({
    "delete", "merge", "transfer", "archive", "rename", "fork", "sync",
})
_FORGE_TIMEOUT = 90


def _looks_like_url(tok: str) -> bool:
    return "://" in tok or tok.endswith(".git") or bool(re.match(r"^[\w.+-]+@", tok))


async def _run_capped(cmd: list, *, cwd: str, timeout: int, progress_cb, subproc_env,
                      not_found_msg: str, timeout_label: str, prefix: str = "") -> dict:
    """Run a subprocess, stream progress, and return a capped {output|error, exit_code}.
    Shared by run_git and run_forge."""
    from src.agent_tools.subprocess_tools import _run_subprocess_streaming
    from src.tool_execution import _truncate
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=subproc_env, cwd=cwd,
        )
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc, timeout=timeout, progress_cb=progress_cb,
        )
    except FileNotFoundError:
        return {"error": not_found_msg, "exit_code": 1}
    if timed_out:
        return {"error": f"{timeout_label}: timed out after {timeout}s", "exit_code": 124}
    output = (stdout.rstrip() + ("\n" + stderr.rstrip() if stderr.strip() else "")).strip()
    return {"output": prefix + (_truncate(output, MAX_OUTPUT_CHARS) or "(no output)"), "exit_code": rc or 0}


def _validate_push(rest: list) -> Optional[str]:
    """Default-deny allowlist for `git push`. Accept only narrow PR-flow forms:
    `push [-u|--set-upstream] origin <branch>` (branch may be HEAD). Reject
    everything else - force/mirror/delete/+refspec/URL targets - by shape."""
    toks = list(rest)
    if toks and toks[0] in ("-u", "--set-upstream"):
        toks = toks[1:]
    if len(toks) != 2:
        return ("git push: only 'push [-u] origin <branch>' is allowed - no flags, "
                "refspecs, deletes, or URL targets.")
    remote, ref = toks
    if remote != "origin":
        return "git push: only the 'origin' remote is allowed (no URL or alternate remotes)."
    if ref.startswith("-") or "+" in ref or ":" in ref:
        return "git push: force/delete/refspec push forms are not allowed."
    if _looks_like_url(ref) or not re.match(r"^(HEAD|[A-Za-z0-9._/-]+)$", ref):
        return "git push: invalid branch target - use a plain branch name or HEAD."
    return None


def _validate_fetch_pull(sub: str, rest: list) -> Optional[str]:
    """Allow only flag-light fetch/pull from origin. Reject executable/path
    override options (--upload-pack, --receive-pack, --exec, -c) and URL targets."""
    for tok in rest:
        if tok.startswith("-"):
            if tok.split("=", 1)[0] not in _FETCH_PULL_SAFE_FLAGS:
                return f"git {sub}: option '{tok}' is not allowed."
        elif _looks_like_url(tok):
            return f"git {sub}: URL targets are not allowed - use the configured 'origin'."
    positionals = [t for t in rest if not t.startswith("-")]
    if positionals and positionals[0] != "origin":
        return f"git {sub}: only the 'origin' remote is allowed."
    return None


def validate_git_argv(argv: list) -> Optional[str]:
    """Return an error string if the git argv is not allowed, else None.
    Shared by the agent tool and the /api/workspace/git endpoint."""
    if not argv:
        return "git: provide a subcommand, e.g. status / diff / commit -m \"msg\"."
    sub = argv[0].lower()
    if sub in _GIT_BLOCKED or sub not in _GIT_ALLOWED:
        return f"git: subcommand '{sub}' is not allowed."
    rest = argv[1:]
    if any(a in _GIT_BANNED_ARGS or a.split("=", 1)[0] in _GIT_BANNED_ARGS for a in rest):
        return "git: path-redirecting options (-C/--git-dir/--work-tree) are not allowed."
    if sub == "remote" and rest and rest[0] not in ("-v", "--verbose", "show", "get-url"):
        return ("git remote: only read-only forms allowed (remote, -v, show, get-url) - "
                "mutating the remote is blocked.")
    if sub == "init" and any(not a.startswith("-") for a in rest):
        return "git init: a target path is not allowed - init operates on the workspace."
    if sub == "push":
        return _validate_push(rest)
    if sub in ("fetch", "pull"):
        return _validate_fetch_pull(sub, rest)
    return None


async def run_git(content: str, workspace: Optional[str], *, progress_cb=None, subproc_env=None) -> dict:
    """Execute a validated git subcommand in the workspace repo."""
    git_bin = shutil.which("git")
    if not git_bin:
        return {"error": "git: not installed on the server.", "exit_code": 1}
    if not workspace:
        return {"error": "git: set a workspace (the repo folder) first.", "exit_code": 1}

    raw = (content or "").strip()
    if raw.lower().startswith("git "):
        raw = raw[4:].strip()
    if not raw:
        return {"error": "git: provide a subcommand, e.g. status / diff / commit -m \"msg\".", "exit_code": 1}
    try:
        argv = shlex.split(raw)
    except ValueError as e:
        return {"error": f"git: could not parse arguments: {e}", "exit_code": 1}

    err = validate_git_argv(argv)
    if err:
        return {"error": err, "exit_code": 1}

    sub = argv[0].lower()
    base = os.path.realpath(workspace)
    cmd = [git_bin, "-C", base]
    # Inject a commit identity so commits work without a configured user.
    if sub == "commit":
        cmd += ["-c", "user.name=Odysseus Agent", "-c", "user.email=agent@odysseus.local"]
    cmd += argv
    return await _run_capped(cmd, cwd=base, timeout=_GIT_TIMEOUT, progress_cb=progress_cb,
                             subproc_env=subproc_env, not_found_msg="git: not installed on the server.",
                             timeout_label=f"git {sub}")


async def run_forge(content: str, workspace: Optional[str], *, progress_cb=None, subproc_env=None) -> dict:
    """Execute a validated forge (gh/glab) command in the workspace repo."""
    if not workspace:
        return {"error": "forge: set a workspace (the repo folder) first.", "exit_code": 1}
    base = os.path.realpath(workspace)
    raw = (content or "").strip()
    for _p in ("gh ", "glab ", "forge "):
        if raw.lower().startswith(_p):
            raw = raw[len(_p):].strip()
    if not raw:
        return {"error": "forge: provide a command, e.g. pr create / pr list / issue view 5.", "exit_code": 1}

    gh_path, glab_path = shutil.which("gh"), shutil.which("glab")

    def _origin_host():
        git_bin = shutil.which("git")
        if not git_bin:
            return ""
        try:
            r = subprocess.run([git_bin, "-C", base, "remote", "get-url", "origin"],
                               capture_output=True, text=True, timeout=5)
            return (r.stdout or "").lower()
        except Exception:
            return ""

    host = await asyncio.to_thread(_origin_host)
    if "gitlab" in host:
        cli, cli_path = ("glab", glab_path) if glab_path else (None, None)
    elif "github" in host:
        cli, cli_path = ("gh", gh_path) if gh_path else (None, None)
    elif gh_path:
        cli, cli_path = "gh", gh_path
    elif glab_path:
        cli, cli_path = "glab", glab_path
    else:
        cli, cli_path = None, None
    if not cli:
        return {"error": "forge: no forge CLI available - install `gh` (GitHub) or `glab` (GitLab) and authenticate it.", "exit_code": 1}

    try:
        argv = shlex.split(raw)
    except ValueError as e:
        return {"error": f"forge: could not parse arguments: {e}", "exit_code": 1}
    # Bridge the PR/MR verb so the agent can always say "pr".
    if cli == "glab" and argv[0].lower() == "pr":
        argv[0] = "mr"
    elif cli == "gh" and argv[0].lower() == "mr":
        argv[0] = "pr"
    top = argv[0].lower()
    if top not in _FORGE_ALLOWED:
        return {"error": f"forge: '{top}' is not allowed (use pr/mr, issue, repo, release, label).", "exit_code": 1}
    subverb = argv[1].lower() if len(argv) > 1 else ""
    if subverb in _FORGE_BLOCKED_SUBVERBS:
        return {"error": f"forge: '{top} {subverb}' is not allowed (destructive). Use read/create forms (list, view, create, comment).", "exit_code": 1}
    return await _run_capped([cli_path, *argv], cwd=base, timeout=_FORGE_TIMEOUT,
                             progress_cb=progress_cb, subproc_env=subproc_env,
                             not_found_msg=f"forge: `{cli}` is not installed on the server.",
                             timeout_label="forge", prefix=f"[{cli}] ")


class GitTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import get_active_workspace
        return await run_git(content, get_active_workspace(),
                             progress_cb=ctx.get("progress_cb"), subproc_env=ctx.get("subproc_env"))


class ForgeTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import get_active_workspace
        return await run_forge(content, get_active_workspace(),
                               progress_cb=ctx.get("progress_cb"), subproc_env=ctx.get("subproc_env"))
