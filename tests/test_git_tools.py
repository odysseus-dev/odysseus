"""Tests for the git + forge tools."""
import os
import shutil
import asyncio
import subprocess
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_git_tools.db")

from src.tool_execution import _direct_fallback
from src.agent_loop import _workspace_git_context


def _run(tool, content, workspace=None):
    return asyncio.run(_direct_fallback(tool, content, workspace=workspace))


@pytest.fixture
def repo(tmp_path):
    d = str(tmp_path)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    (tmp_path / "a.txt").write_text("hello\n")
    return d


# ── git: gating ─────────────────────────────────────────────────────────────

def test_git_requires_workspace():
    r = _run("git", "status", workspace=None)
    assert r["exit_code"] == 1 and "workspace" in r["error"]


def test_git_blocked_subcommand(repo):
    for bad in ("config user.email x@y.z", "clone https://x/y", "daemon"):
        r = _run("git", bad, workspace=repo)
        assert r["exit_code"] == 1 and "not allowed" in r["error"], bad


def test_git_unknown_subcommand(repo):
    r = _run("git", "frobnicate", workspace=repo)
    assert r["exit_code"] == 1 and "not allowed" in r["error"]


def test_git_strips_leading_git(repo):
    r = _run("git", "git status", workspace=repo)  # model included "git "
    assert r["exit_code"] == 0
    assert "a.txt" in r["output"]  # untracked file shows in status


# ── git: read + write ────────────────────────────────────────────────────────

def test_git_status(repo):
    r = _run("git", "status --porcelain", workspace=repo)
    assert r["exit_code"] == 0
    assert "a.txt" in r["output"]


def test_git_add_commit_with_injected_identity(repo):
    # No user.name/email configured in this repo — commit must still work.
    assert _run("git", "add -A", workspace=repo)["exit_code"] == 0
    c = _run("git", 'commit -m "init"', workspace=repo)
    assert c["exit_code"] == 0
    log = _run("git", "log --oneline", workspace=repo)
    assert "init" in log["output"]
    # Identity came from the injected -c flags.
    who = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%an <%ae>"],
                         capture_output=True, text=True)
    assert "Odysseus Agent" in who.stdout and "agent@odysseus.local" in who.stdout


def test_git_branch_create(repo):
    _run("git", "add -A", workspace=repo)
    _run("git", 'commit -m "init"', workspace=repo)
    assert _run("git", "checkout -b feature", workspace=repo)["exit_code"] == 0
    assert "feature" in _run("git", "branch", workspace=repo)["output"]


# ── forge: gating (no network) ───────────────────────────────────────────────

def test_forge_requires_workspace():
    r = _run("forge", "pr list", workspace=None)
    assert r["exit_code"] == 1 and "workspace" in r["error"]


def test_forge_blocked_subcommand(repo):
    # 'auth' is not in the allowlist → rejected before any CLI runs.
    r = _run("forge", "auth status", workspace=repo)
    assert r["exit_code"] == 1 and "not allowed" in r["error"]


# ── workspace git/forge context (system-prompt note) ────────────────────────

def test_gitctx_non_repo(tmp_path):
    assert _workspace_git_context(str(tmp_path)) == ""
    assert _workspace_git_context("") == ""
    assert _workspace_git_context("/no/such/dir") == ""


def test_gitctx_repo_no_remote(repo):
    out = _workspace_git_context(repo)
    assert "## GIT" in out
    assert "git repo" in out
    assert "No GitHub/GitLab remote" in out


def test_gitctx_github_remote(repo):
    subprocess.run(["git", "-C", repo, "remote", "add", "origin",
                    "https://github.com/foo/bar.git"], check=True)
    out = _workspace_git_context(repo)
    assert "GitHub" in out
    # forge guidance depends on whether gh is installed on the host
    if shutil.which("gh"):
        assert "forge" in out and "gh" in out
    else:
        assert "isn't installed" in out


def test_gitctx_gitlab_remote(repo):
    subprocess.run(["git", "-C", repo, "remote", "add", "origin",
                    "https://gitlab.com/foo/bar.git"], check=True)
    out = _workspace_git_context(repo)
    assert "GitLab" in out
    if not shutil.which("glab"):
        assert "isn't installed" in out


def test_gitctx_unborn_branch_repo(tmp_path):
    # Fresh repo, no commits — should still be detected as a repo.
    d = str(tmp_path)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    out = _workspace_git_context(d)
    assert "## GIT" in out and "git repo" in out


# ── git: argument-level policy (alteixeira20 review) ────────────────────────

def test_git_remote_mutation_blocked(repo):
    for bad in ("remote add origin https://evil.example/x.git",
                "remote set-url origin https://evil2.example/x.git",
                "remote remove origin",
                "remote rename origin upstream"):
        r = _run("git", bad, workspace=repo)
        assert r["exit_code"] == 1 and "read-only" in r["error"], bad


def test_git_remote_readonly_allowed(repo):
    subprocess.run(["git", "-C", repo, "remote", "add", "origin",
                    "https://github.com/foo/bar.git"], check=True)
    r = _run("git", "remote -v", workspace=repo)
    assert r["exit_code"] == 0 and "origin" in r["output"]


def test_git_init_with_path_blocked(repo, tmp_path):
    outside = str(tmp_path / "outside-created-by-init")
    r = _run("git", f"init {outside}", workspace=repo)
    assert r["exit_code"] == 1 and "not allowed" in r["error"]
    assert not os.path.exists(outside)


def test_git_path_redirect_option_blocked(repo):
    for bad in ("status -C /tmp", "log --git-dir=/tmp/.git", "status --work-tree /tmp"):
        r = _run("git", bad, workspace=repo)
        assert r["exit_code"] == 1 and "not allowed" in r["error"], bad


# ── forge: destructive second-level verbs rejected before reaching the CLI ───

def _fake_forge_cli(tmp_path, monkeypatch, name="gh"):
    binp = tmp_path / "bin"
    binp.mkdir(exist_ok=True)
    f = binp / name
    f.write_text("#!/bin/sh\necho should-not-run\nexit 0\n")
    f.chmod(0o755)
    monkeypatch.setenv("PATH", str(binp) + os.pathsep + os.environ.get("PATH", ""))


def test_forge_destructive_subverb_blocked(repo, tmp_path, monkeypatch):
    _fake_forge_cli(tmp_path, monkeypatch, "gh")
    subprocess.run(["git", "-C", repo, "remote", "add", "origin",
                    "https://github.com/foo/bar.git"], check=True)
    for bad in ("repo delete foo/bar --yes", "release delete v1.0.0 --yes",
                "issue delete 1 --yes", "label delete production --yes",
                "pr merge 1 --squash --delete-branch"):
        r = _run("forge", bad, workspace=repo)
        assert r["exit_code"] == 1 and "not allowed" in r["error"], bad
        assert "should-not-run" not in (r.get("output") or ""), bad


# ── /api/workspace/git route: worktree validation ──────────────────────────

def test_workspace_git_route_rejects_non_repo(tmp_path, monkeypatch):
    import routes.workspace_routes as wr
    from unittest.mock import MagicMock
    monkeypatch.setattr(wr, "get_current_user", lambda r: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda o: True)
    router = wr.setup_workspace_routes()
    ep = next(r.endpoint for r in router.routes
              if getattr(r, "path", "").endswith("/git") and "POST" in getattr(r, "methods", set()))
    with pytest.raises(Exception) as ei:
        asyncio.run(ep(request=MagicMock(), command="status", path=str(tmp_path)))
    assert "git repository" in str(ei.value)
