"""In-app self-update from the upstream GitHub repository.

Pulls new commits from the community/owner repo (`upstream/<branch>`) on top of
the local checkout — which carries local customizations as commits ahead of
upstream — using a *conflict-safe* merge: if the merge can't apply cleanly it is
aborted and nothing changes, so local customizations are never lost. On success
it reinstalls deps (only if requirements changed), restores full chromadb for the
native macOS install, regenerates the service-worker build id, and asks the user
to restart (no forced restart — an active session is preserved).

All operations are admin-gated at the route layer. The module degrades to
``is_supported() == False`` for frozen/packaged builds or checkouts without an
``upstream`` remote, so the UI can hide the feature.
"""

import hashlib
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from src.constants import APP_VERSION
from src.runtime_paths import get_app_root

REPO_DIR = get_app_root()

# The "main project repo" to track. Overridable for testing / non-default forks.
UPSTREAM_REMOTE = os.getenv("ODYSSEUS_UPDATE_REMOTE", "upstream")
UPSTREAM_BRANCH = os.getenv("ODYSSEUS_UPDATE_BRANCH", "dev")
UPSTREAM_REF = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"

# Generated, gitignored. sw.js importScripts() this so CACHE_NAME tracks a build
# id instead of a committed version number that would conflict on every merge.
SW_BUILD_FILE = os.path.join(REPO_DIR, "static", "sw-build.js")


def _git(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a git command in the repo. Never raises on non-zero exit — callers
    inspect ``.returncode`` so a failed git call becomes a structured result
    rather than a 500."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def is_supported() -> bool:
    """True only when self-update can actually work: a real git checkout (not a
    frozen bundle) that has the configured upstream remote."""
    if getattr(sys, "frozen", False):
        return False
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return False
    remotes = _git("remote").stdout.split()
    return UPSTREAM_REMOTE in remotes


def _file_hash_at(ref: str, path: str) -> Optional[str]:
    """sha256 of a tracked file as it exists at a git ref, or None if absent."""
    r = _git("show", f"{ref}:{path}")
    if r.returncode != 0:
        return None
    return hashlib.sha256(r.stdout.encode("utf-8", "replace")).hexdigest()


def _predict_conflicts(head: str, target: str) -> List[str]:
    """Files that would conflict if ``target`` were merged into ``head`` — found
    via ``git merge-tree --write-tree`` (git >= 2.38), which computes the merge
    in memory WITHOUT touching the working tree. Exit 0 = clean, 1 = conflicts."""
    r = _git("merge-tree", "--write-tree", "--name-only", head, target)
    if r.returncode == 0:
        return []
    lines = r.stdout.splitlines()
    # Output: line 0 is the merged tree OID; the conflicted-file section follows,
    # terminated by a blank line before any informational messages.
    files: List[str] = []
    for line in lines[1:]:
        if line.strip() == "":
            break
        files.append(line.strip())
    return files


def _venv_python() -> str:
    """The repo venv's python (used by the native launcher); fall back to the
    running interpreter."""
    p = os.path.join(REPO_DIR, "venv", "bin", "python")
    return p if os.path.exists(p) else sys.executable


def _reinstall_deps() -> bool:
    """pip install -r requirements.txt, then restore full chromadb. Mirrors
    ~/.local/bin/odysseus-update: requirements.txt ships chromadb-client (Docker
    HTTP-only), but the native macOS install needs the full chromadb package for
    the local server CLI — swap it back if pip pulled in the client."""
    py = _venv_python()
    try:
        subprocess.run(
            [py, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=1800, check=True,
        )
    except Exception:
        return False
    try:
        if subprocess.run(
            [py, "-m", "pip", "show", "chromadb-client"],
            cwd=REPO_DIR, capture_output=True, text=True,
        ).returncode == 0:
            subprocess.run([py, "-m", "pip", "uninstall", "-y", "chromadb-client"],
                           cwd=REPO_DIR, capture_output=True, text=True, timeout=600)
            subprocess.run([py, "-m", "pip", "install", "--force-reinstall", "chromadb"],
                           cwd=REPO_DIR, capture_output=True, text=True, timeout=1800)
    except Exception:
        pass  # the deps install above already succeeded; chromadb swap is best-effort
    return True


def _write_sw_build(head: str) -> None:
    """Regenerate the service-worker build id so the PWA cache refreshes after an
    update without a committed CACHE_NAME bump."""
    build = f"{head[:10]}-{int(time.time())}"
    try:
        os.makedirs(os.path.dirname(SW_BUILD_FILE), exist_ok=True)
        with open(SW_BUILD_FILE, "w", encoding="utf-8") as f:
            f.write(
                "// Generated by src/self_update.py — gitignored. Drives the SW "
                "cache name.\nself.SW_BUILD = '%s';\n" % build
            )
    except OSError:
        pass


def _notify(message: str) -> None:
    """Best-effort macOS notification, matching the odysseus-update UX."""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "Odysseus"'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def check_updates() -> Dict[str, Any]:
    """Fetch upstream and report what an update would bring, without changing
    anything. Returns a JSON-able dict for the admin UI."""
    if not is_supported():
        return {"supported": False}

    fetch = _git("fetch", UPSTREAM_REMOTE, "--tags", timeout=180)
    if fetch.returncode != 0:
        return {"supported": True, "status": "error",
                "detail": (fetch.stderr or "git fetch failed").strip()}

    if _git("rev-parse", "--verify", "--quiet", UPSTREAM_REF).returncode != 0:
        return {"supported": True, "status": "error",
                "detail": f"upstream ref {UPSTREAM_REF} not found"}

    head = _git("rev-parse", "HEAD").stdout.strip()
    behind = int((_git("rev-list", "--count", f"HEAD..{UPSTREAM_REF}").stdout or "0").strip() or 0)
    ahead = int((_git("rev-list", "--count", f"{UPSTREAM_REF}..HEAD").stdout or "0").strip() or 0)

    if behind == 0:
        return {"supported": True, "status": "up_to_date", "version": APP_VERSION,
                "current": head[:10], "ahead": ahead}

    changelog = _git("log", "--oneline", "--no-decorate", "-n", "50",
                     f"HEAD..{UPSTREAM_REF}").stdout.splitlines()
    requirements_changed = (
        _file_hash_at("HEAD", "requirements.txt")
        != _file_hash_at(UPSTREAM_REF, "requirements.txt")
    )
    return {
        "supported": True,
        "status": "update_available",
        "version": APP_VERSION,
        "current": head[:10],
        "target": _git("rev-parse", "--short", UPSTREAM_REF).stdout.strip(),
        "behind": behind,
        "ahead": ahead,
        "changelog": changelog,
        "requirements_changed": requirements_changed,
        "predicted_conflicts": _predict_conflicts(head, UPSTREAM_REF),
    }


def apply_update() -> Dict[str, Any]:
    """Conflict-safe merge of upstream into the local branch. On conflict the
    merge is aborted and the repo is left exactly as it was."""
    if not is_supported():
        return {"status": "unsupported"}

    if _git("status", "--porcelain").stdout.strip():
        return {"status": "dirty",
                "detail": "working tree has uncommitted changes; commit or stash them first"}

    fetch = _git("fetch", UPSTREAM_REMOTE, "--tags", timeout=180)
    if fetch.returncode != 0:
        return {"status": "error", "detail": (fetch.stderr or "git fetch failed").strip()}

    if _git("rev-parse", "--verify", "--quiet", UPSTREAM_REF).returncode != 0:
        return {"status": "error", "detail": f"upstream ref {UPSTREAM_REF} not found"}

    head_before = _git("rev-parse", "HEAD").stdout.strip()
    if _git("merge-base", "--is-ancestor", UPSTREAM_REF, "HEAD").returncode == 0:
        return {"status": "up_to_date", "head": head_before[:10]}

    req_before = _file_hash_at("HEAD", "requirements.txt")

    # Safety net: an annotated tag at the pre-merge commit, so the update is
    # always reversible with `git reset --hard <tag>`.
    backup_tag = f"odysseus-pre-update-{time.strftime('%Y%m%d-%H%M%S')}"
    _git("tag", backup_tag, head_before)

    merge = _git("merge", "--no-edit", UPSTREAM_REF, timeout=600)
    if merge.returncode != 0:
        conflicts = _git("diff", "--name-only", "--diff-filter=U").stdout.split()
        _git("merge", "--abort")
        return {"status": "conflict", "conflicts": conflicts, "backup_tag": backup_tag,
                "detail": "merge aborted — nothing was changed; your customizations are intact"}

    head_after = _git("rev-parse", "HEAD").stdout.strip()
    commits = _git("log", "--oneline", "--no-decorate",
                   f"{head_before}..{head_after}").stdout.splitlines()

    deps_reinstalled = False
    if req_before != _file_hash_at(head_after, "requirements.txt"):
        deps_reinstalled = _reinstall_deps()

    _write_sw_build(head_after)
    _notify(f"Atualizado para {head_after[:10]} — reinicie o Odysseus para aplicar.")

    return {
        "status": "applied",
        "from": head_before[:10],
        "to": head_after[:10],
        "commits": commits,
        "deps_reinstalled": deps_reinstalled,
        "backup_tag": backup_tag,
        "restart_needed": True,
    }
