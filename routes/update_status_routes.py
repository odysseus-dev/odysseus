"""Update-status routes — check-only software update state probes."""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse
import os
import subprocess

from fastapi import APIRouter, Request

from core.middleware import require_admin
from core.constants import APP_VERSION, BASE_DIR


GIT_TIMEOUT_SECONDS = 1.8

_MODE_COMMANDS = {
    "source_docker": [
        "git fetch --prune",
        "git pull --ff-only",
        "docker compose up -d --build",
    ],
    "source_native": [
        "git pull --ff-only",
        "python -m pip install -r requirements.txt",
        "python setup.py",
    ],
    "prebuilt_docker": [
        "docker compose pull",
        "docker compose up -d",
    ],
}


def _scrub_remote_url(remote_url: Optional[str]) -> Optional[str]:
    """Remove credentials from a git remote URL so tokens are never returned."""
    if not remote_url:
        return None

    remote_url = remote_url.strip()
    if "://" not in remote_url:
        # scp-style URLs like git@host:owner/repo.git are returned untouched.
        return remote_url

    parsed = urlparse(remote_url)
    if not (parsed.username or parsed.password):
        return remote_url

    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


def _remote_url_allowed(remote_url: Optional[str]) -> bool:
    """Return True for normal git remotes that are safe to inspect."""
    if not remote_url:
        return False
    value = remote_url.strip()
    if not value:
        return False
    if "://" in value:
        scheme = urlparse(value).scheme.lower()
        return scheme in {"https", "http", "ssh", "git"}
    # SCP-style SSH remotes, for example git@github.com:owner/repo.git.
    return "@" in value and ":" in value and not value.startswith(("/", "."))


def _run_git(cwd: str, args: list[str], timeout: float = GIT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run a fixed git command against a checkout with a bounded timeout."""
    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
        return {
            "ok": True,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": "git_missing", "details": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "timeout", "details": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive.
        return {"ok": False, "error": "unexpected", "details": str(exc)}


def _run_output(cwd: str, args: list[str], *, command_name: str, base: dict[str, Any], timeout_tag: str) -> Optional[str]:
    """Run a git command and record timeout/error info in `base`."""
    result = _run_git(cwd, args)
    if not result.get("ok"):
        if result.get("error") == "timeout":
            base["git"]["timeouts"].append(timeout_tag)
        else:
            base["git"]["errors"].append(f"{command_name} failed")
        return None
    if result.get("returncode") != 0:
        return None
    return (result.get("stdout") or "").strip()


def _parse_head_offset(raw: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Parse `rev-list --left-right --count` output into (ahead, behind)."""
    if not raw:
        return None, None
    parts = raw.split()
    if len(parts) != 2:
        return None, None
    if not parts[0].isdigit() or not parts[1].isdigit():
        return None, None
    return int(parts[0]), int(parts[1])


def _parse_for_each_ref(raw: Optional[str]) -> Dict[str, str]:
    """Parse `for-each-ref --format='%(refname:short) %(upstream:short)'` output."""
    mapping: Dict[str, str] = {}
    if not raw:
        return mapping

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        local, _, upstream = line.partition(" ")
        if local and upstream:
            mapping[local] = upstream.strip()
    return mapping


def _detect_install_mode(git_available: bool, repo_dir: str, in_container: bool) -> str:
    has_compose = os.path.exists(os.path.join(repo_dir, "docker-compose.yml"))
    if git_available and has_compose:
        return "source_docker"
    if git_available:
        return "source_native"
    if in_container:
        return "prebuilt_docker"
    return "source_native"


def _collect_update_status(repo_dir: str | None = None) -> Dict[str, Any]:
    repo_dir = os.path.abspath(repo_dir or BASE_DIR)
    in_container = os.path.exists("/.dockerenv")

    base: Dict[str, Any] = {
        "version": APP_VERSION,
        "git": {
            "available": False,
            "commit": None,
            "branch": None,
            "detached_head": False,
            "dirty": None,
            "upstream": {
                "remote": None,
                "remote_branch": None,
                "remote_url_scrubbed": None,
                "tracking_configured": False,
            },
            "remote": {
                "sha": None,
            },
            "ahead": None,
            "behind": None,
            "warnings": [],
            "errors": [],
            "timeouts": [],
        },
        "install": {
            "mode_guess": None,
            "manual_update_commands": [],
            "manual_update_commands_by_mode": dict(_MODE_COMMANDS),
        },
    }

    toplevel = _run_output(
        repo_dir,
        ["rev-parse", "--show-toplevel"],
        command_name="rev-parse --show-toplevel",
        base=base,
        timeout_tag="rev-parse --show-toplevel",
    )
    if not toplevel:
        base["install"]["mode_guess"] = _detect_install_mode(False, repo_dir, in_container)
        base["install"]["manual_update_commands"] = _MODE_COMMANDS[base["install"]["mode_guess"]]
        if not base["git"]["errors"]:
            base["git"]["errors"].append("Repository is not a git checkout.")
            base["git"]["warnings"].append("Git state unavailable; no repository metadata.")
        return base

    base["git"]["available"] = True

    commit = _run_output(
        repo_dir,
        ["rev-parse", "--short", "HEAD"],
        command_name="rev-parse --short HEAD",
        base=base,
        timeout_tag="rev-parse --short HEAD",
    )
    if commit:
        base["git"]["commit"] = commit

    branch = _run_output(
        repo_dir,
        ["rev-parse", "--abbrev-ref", "HEAD"],
        command_name="rev-parse --abbrev-ref HEAD",
        base=base,
        timeout_tag="rev-parse --abbrev-ref HEAD",
    )
    if branch:
        base["git"]["branch"] = branch
        base["git"]["detached_head"] = branch == "HEAD"
    else:
        base["git"]["warnings"].append("Unable to resolve current HEAD branch.")

    status = _run_output(
        repo_dir,
        ["status", "--porcelain"],
        command_name="status --porcelain",
        base=base,
        timeout_tag="status --porcelain",
    )
    if status is not None:
        base["git"]["dirty"] = bool(status.strip())
    else:
        base["git"]["dirty"] = None

    if branch and not base["git"]["detached_head"]:
        branch_remote = _run_output(
            repo_dir,
            ["config", "--get", f"branch.{branch}.remote"],
            command_name="config --get branch.<branch>.remote",
            base=base,
            timeout_tag="config --get branch remote",
        )
        branch_merge = _run_output(
            repo_dir,
            ["config", "--get", f"branch.{branch}.merge"],
            command_name="config --get branch.<branch>.merge",
            base=base,
            timeout_tag="config --get branch merge",
        )
        if branch_merge and branch_merge.startswith("refs/heads/"):
            branch_merge = branch_merge.replace("refs/heads/", "", 1)

        for_each_ref = _run_output(
            repo_dir,
            ["for-each-ref", "--format=%(refname:short) %(upstream:short)", "refs/heads"],
            command_name="for-each-ref upstream mapping",
            base=base,
            timeout_tag="for-each-ref",
        )
        for_each_map = _parse_for_each_ref(for_each_ref)

        upstream = None
        if branch in for_each_map:
            upstream = for_each_map[branch]
        if not branch_remote and upstream and "/" in upstream:
            branch_remote = upstream.split("/", 1)[0]
            if not branch_merge:
                branch_merge = upstream.split("/", 1)[1]
        if branch_remote and branch_merge:
            upstream = f"{branch_remote}/{branch_merge}"

        if branch_remote:
            base["git"]["upstream"]["remote"] = branch_remote
        if upstream:
            base["git"]["upstream"]["tracking_configured"] = True
            if branch_merge:
                base["git"]["upstream"]["remote_branch"] = branch_merge
            else:
                base["git"]["upstream"]["remote_branch"] = upstream.split("/", 1)[1] if "/" in upstream else upstream

            remote_url = _run_output(
                repo_dir,
                ["config", "--get", f"remote.{branch_remote}.url"],
                command_name="config --get remote URL",
                base=base,
                timeout_tag="config --get remote url",
            )
            if remote_url:
                scrubbed_remote = _scrub_remote_url(remote_url)
                base["git"]["upstream"]["remote_url_scrubbed"] = scrubbed_remote

            remote_ref = branch_merge or upstream.split("/", 1)[-1]
            if remote_url and not _remote_url_allowed(remote_url):
                base["git"]["warnings"].append("Remote URL scheme is not supported for update checks.")
            elif branch_remote and remote_ref:
                remote_head = _run_output(
                    repo_dir,
                    ["ls-remote", "--heads", branch_remote, remote_ref],
                    command_name="ls-remote --heads",
                    base=base,
                    timeout_tag="ls-remote --heads",
                )
                if remote_head:
                    base["git"]["remote"]["sha"] = remote_head.split()[0]

            ahead_behind = _run_output(
                repo_dir,
                ["rev-list", "--left-right", "--count", "HEAD...@{u}"],
                command_name="rev-list --left-right --count",
                base=base,
                timeout_tag="rev-list --left-right --count",
            )
            if ahead_behind:
                ahead, behind = _parse_head_offset(ahead_behind)
                base["git"]["ahead"] = ahead
                base["git"]["behind"] = behind
        else:
            base["git"]["warnings"].append("No upstream tracking branch configured.")
    else:
        if branch:
            base["git"]["warnings"].append("Detached HEAD; no branch-level update checks available.")

    if not base["git"]["warnings"]:
        if base["git"]["dirty"]:
            base["git"]["warnings"].append("Repository contains uncommitted changes.")

    if base["git"]["detached_head"]:
        base["git"]["upstream"]["tracking_configured"] = False

    base["install"]["mode_guess"] = _detect_install_mode(base["git"]["available"], repo_dir, in_container)
    base["install"]["manual_update_commands"] = _MODE_COMMANDS[base["install"]["mode_guess"]]

    return base


def setup_update_status_routes() -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    @router.get("/update/status")
    def get_update_status(request: Request) -> Dict[str, Any]:
        """Check-only software-update observability for admin users."""
        require_admin(request)
        return _collect_update_status()

    return router
