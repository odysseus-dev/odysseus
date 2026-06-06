from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.tool_execution import _is_sensitive_path


GIT_TIMEOUT_SECONDS = 30
TEXT_PREVIEW_LIMIT = 512 * 1024
SKIPPED_DIRS = {".git", "node_modules", ".venv", "__pycache__"}
MAX_STATUS_FILES = 1000
MAX_DIFF_BYTES = 2 * 1024 * 1024
MAX_BLAME_BYTES = 1024 * 1024
MAX_CONFLICT_SCAN_FILES = 2000
_MUTATION_LOCKS: dict[str, threading.Lock] = {}
_MUTATION_LOCKS_GUARD = threading.Lock()


class GitWorkspaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RepoContext:
    workspace: str
    repo_root: str
    prefix: str


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _has_forbidden_component(path: str) -> bool:
    return any(part in SKIPPED_DIRS for part in Path(path).parts)


def resolve_workspace(workspace: str | None) -> str:
    if workspace is None or not str(workspace).strip():
        raise GitWorkspaceError("invalid_workspace", "workspace is required")
    resolved = os.path.realpath(os.path.expanduser(str(workspace).strip()))
    if not os.path.isdir(resolved):
        raise GitWorkspaceError("invalid_workspace", "workspace must be an existing directory")
    if _is_sensitive_path(resolved):
        raise GitWorkspaceError("outside_workspace", "workspace is sensitive")
    return resolved


def resolve_workspace_path(
    workspace: str,
    path: str | None,
    *,
    must_exist: bool = False,
    allow_absolute: bool = False,
) -> str:
    base = resolve_workspace(workspace)
    rel = "" if path is None else str(path).strip()
    if os.path.isabs(rel) and not allow_absolute:
        raise GitWorkspaceError("outside_workspace", "path must be workspace-relative")
    expanded = os.path.expanduser(rel)
    candidate = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
    resolved = os.path.realpath(candidate)
    if _is_sensitive_path(resolved):
        raise GitWorkspaceError("outside_workspace", "path is sensitive")
    rel_to_base = os.path.relpath(resolved, base)
    if rel_to_base != "." and _has_forbidden_component(rel_to_base):
        raise GitWorkspaceError("outside_workspace", "path is not editable")
    if resolved != base:
        try:
            if os.path.commonpath([_norm(resolved), _norm(base)]) != _norm(base):
                raise ValueError
        except ValueError as exc:
            raise GitWorkspaceError("outside_workspace", "path is outside the workspace") from exc
    if must_exist and not os.path.exists(resolved):
        raise GitWorkspaceError("outside_workspace", "path does not exist")
    return resolved


def workspace_rel(workspace: str, absolute: str) -> str:
    rel = os.path.relpath(os.path.realpath(absolute), resolve_workspace(workspace))
    return "" if rel == "." else rel.replace(os.sep, "/")


def _is_binary_sample(data: bytes) -> bool:
    return b"\x00" in data


def list_workspace_files(workspace: str, path: str | None = "") -> dict[str, Any]:
    base = resolve_workspace(workspace)
    target = resolve_workspace_path(base, path or "", must_exist=True)
    if not os.path.isdir(target):
        raise GitWorkspaceError("invalid_workspace", "path must be a directory")
    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    try:
        entries = list(os.scandir(target))
    except OSError as exc:
        raise GitWorkspaceError("invalid_workspace", str(exc)) from exc
    for entry in entries:
        if entry.name.startswith(".") or entry.name in SKIPPED_DIRS:
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
            rel = workspace_rel(base, os.path.join(target, entry.name))
            item = {"name": entry.name, "path": rel, "mtime": stat.st_mtime}
            if entry.is_dir(follow_symlinks=False):
                dirs.append(item)
            elif entry.is_file(follow_symlinks=False):
                item["size"] = stat.st_size
                files.append(item)
        except OSError:
            continue
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    parent_abs = os.path.dirname(target)
    parent = None if _norm(parent_abs) == _norm(base) or _norm(target) == _norm(base) else workspace_rel(base, parent_abs)
    return {
        "ok": True,
        "workspace": base,
        "path": workspace_rel(base, target),
        "parent": parent,
        "dirs": dirs,
        "files": files,
    }


def read_workspace_file(workspace: str, path: str) -> dict[str, Any]:
    base = resolve_workspace(workspace)
    target = resolve_workspace_path(base, path, must_exist=True)
    if not os.path.isfile(target):
        raise GitWorkspaceError("outside_workspace", "path must be a file")
    stat = os.stat(target)
    with open(target, "rb") as fh:
        sample = fh.read(TEXT_PREVIEW_LIMIT + 1)
    binary = _is_binary_sample(sample)
    out = {
        "ok": True,
        "path": workspace_rel(base, target),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "binary": binary,
        "editable": not binary,
    }
    if not binary:
        out["content"] = sample[:TEXT_PREVIEW_LIMIT].decode("utf-8", errors="replace")
    return out


def save_workspace_file(workspace: str, path: str, content: str) -> dict[str, Any]:
    if not isinstance(content, str):
        raise GitWorkspaceError("binary_file", "content must be text")
    base = resolve_workspace(workspace)
    if not str(path or "").strip():
        raise GitWorkspaceError("invalid_request", "path is required")
    target = resolve_workspace_path(base, path, must_exist=False)
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise GitWorkspaceError("outside_workspace", "parent directory does not exist")
    encoded = content.encode("utf-8")
    if _is_binary_sample(encoded):
        raise GitWorkspaceError("binary_file", "content must be text")
    try:
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        stat = os.stat(target)
    except IsADirectoryError as exc:
        raise GitWorkspaceError("invalid_request", "path must be a file") from exc
    except OSError as exc:
        raise GitWorkspaceError("git_failed", str(exc)) from exc
    return {"ok": True, "path": workspace_rel(base, target), "size": stat.st_size, "mtime": stat.st_mtime}


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {}
    for key, value in os.environ.items():
        if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG"}:
            continue
        if key.startswith("GIT_CONFIG_"):
            continue
        env[key] = value
    hardened = {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "protocol.ext.allow",
        "GIT_CONFIG_VALUE_0": "never",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": os.devnull,
        "GIT_CONFIG_KEY_2": "diff.external",
        "GIT_CONFIG_VALUE_2": "",
    }
    env.update(hardened)
    if extra:
        offset = int(env["GIT_CONFIG_COUNT"])
        config_items = [(k, v) for k, v in extra.items() if k.startswith("GIT_CONFIG_KEY_")]
        if config_items:
            raise GitWorkspaceError("git_failed", "internal git config override is not supported")
        env.update(extra)
        if "GIT_INDEX_FILE" in extra:
            env["GIT_CONFIG_COUNT"] = str(offset)
    return env


def _raw_git_run(repo_root: str, args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise GitWorkspaceError("missing_git", "git is not installed")
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
            env=_git_env(env),
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitWorkspaceError("missing_git", "git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitWorkspaceError("git_failed", "git command timed out") from exc


def _reject_unsafe_git_config(repo_root: str) -> None:
    result = _raw_git_run(repo_root, ["config", "--local", "--get-regexp", r"^(filter\..*\.(clean|process)|core\.fsmonitor|core\.sshcommand)$"])
    if result.returncode == 0 and result.stdout.strip():
        raise GitWorkspaceError("git_failed", "unsafe git config blocks this operation")


def git_run(repo_root: str, args: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = _raw_git_run(repo_root, args, env=env)
    if check and result.returncode != 0:
        stderr = (result.stderr or result.stdout or "git command failed").strip()
        code = "merge_conflict" if "conflict" in stderr.lower() else "git_failed"
        raise GitWorkspaceError(code, stderr)
    return result


def repo_context(workspace: str) -> RepoContext:
    ws = resolve_workspace(workspace)
    result = git_run(ws, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        raise GitWorkspaceError("not_git_repo", "workspace is not inside a git repository")
    root = os.path.realpath(result.stdout.strip())
    try:
        if os.path.commonpath([_norm(ws), _norm(root)]) != _norm(root):
            raise ValueError
    except ValueError as exc:
        raise GitWorkspaceError("outside_workspace", "repository is outside workspace") from exc
    prefix = os.path.relpath(ws, root)
    return RepoContext(workspace=ws, repo_root=root, prefix="" if prefix == "." else prefix.replace(os.sep, "/"))


def _repo_rel(ctx: RepoContext, workspace_path: str) -> str:
    resolved = resolve_workspace_path(ctx.workspace, workspace_path, must_exist=False)
    rel = os.path.relpath(resolved, ctx.repo_root).replace(os.sep, "/")
    if rel in {"", "."} or rel.startswith("../"):
        raise GitWorkspaceError("outside_workspace", "path must be a workspace-relative file path")
    return rel


def _workspace_pathspec(ctx: RepoContext) -> list[str]:
    return [ctx.prefix] if ctx.prefix else []


def _to_workspace_rel(ctx: RepoContext, repo_path: str | None) -> str | None:
    if repo_path is None:
        return None
    clean = repo_path.replace("\\", "/")
    if not ctx.prefix:
        return clean
    prefix = ctx.prefix.rstrip("/") + "/"
    if clean == ctx.prefix:
        return ""
    if clean.startswith(prefix):
        return clean[len(prefix):]
    return clean


def _validate_paths(ctx: RepoContext, paths: list[str] | None) -> list[str]:
    if not paths:
        raise GitWorkspaceError("outside_workspace", "at least one path is required")
    rels = []
    for path in paths:
        if not isinstance(path, str) or not path.strip() or path.strip() in {".", "./"}:
            raise GitWorkspaceError("outside_workspace", "path must be a workspace-relative file path")
        rels.append(_repo_rel(ctx, path))
    return rels


def _require_repo_root_workspace(ctx: RepoContext) -> None:
    if ctx.prefix:
        raise GitWorkspaceError("outside_workspace", "operation requires the repository root workspace")


def _to_workspace_paths(ctx: RepoContext, rels: list[str]) -> list[str]:
    return [path for path in (_to_workspace_rel(ctx, rel) for rel in rels) if path is not None]


def _mutation_lock(repo_root: str) -> threading.Lock:
    key = os.path.realpath(repo_root)
    with _MUTATION_LOCKS_GUARD:
        return _MUTATION_LOCKS.setdefault(key, threading.Lock())


def _porcelain_kind(value: str) -> str | None:
    return {
        ".": None,
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "U": "unmerged",
        "?": "untracked",
        "!": "ignored",
    }.get(value, value.lower() if value and value != "." else None)


def git_status(workspace: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    branch = git_run(ctx.repo_root, ["branch", "--show-current"], check=False).stdout.strip()
    upstream = git_run(ctx.repo_root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False)
    upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else None
    ahead = behind = 0
    if upstream_name:
        counts = git_run(ctx.repo_root, ["rev-list", "--left-right", "--count", f"{upstream_name}...HEAD"], check=False)
        if counts.returncode == 0:
            parts = counts.stdout.strip().split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])

    args = ["status", "--porcelain=v2", "--branch", "--untracked-files=all"]
    pathspec = _workspace_pathspec(ctx)
    if pathspec:
        args.extend(["--", *pathspec])
    result = git_run(ctx.repo_root, args)
    files: list[dict[str, Any]] = []
    truncated = False
    for line in result.stdout.splitlines():
        if len(files) >= MAX_STATUS_FILES:
            truncated = True
            break
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if line.startswith("? "):
            files.append({"path": _to_workspace_rel(ctx, line[2:]), "index": None, "worktree": "untracked", "raw": "??"})
            continue
        parts = line.split(" ")
        if line.startswith("1 ") and len(parts) >= 9:
            xy = parts[1]
            files.append({"path": _to_workspace_rel(ctx, " ".join(parts[8:])), "index": _porcelain_kind(xy[0]), "worktree": _porcelain_kind(xy[1]), "raw": xy})
        elif line.startswith("2 ") and len(parts) >= 10:
            xy = parts[1]
            path_field = " ".join(parts[9:])
            new_path, _, old_path = path_field.partition("\t")
            files.append({
                "path": _to_workspace_rel(ctx, new_path),
                "origPath": _to_workspace_rel(ctx, old_path) if old_path else None,
                "index": _porcelain_kind(xy[0]),
                "worktree": _porcelain_kind(xy[1]),
                "raw": xy,
            })
        elif line.startswith("u ") and len(parts) >= 11:
            files.append({"path": _to_workspace_rel(ctx, " ".join(parts[10:])), "index": "unmerged", "worktree": "unmerged", "raw": parts[1]})
    return {
        "ok": True,
        "workspace": ctx.workspace,
        "repoRoot": ctx.repo_root,
        "branch": branch,
        "upstream": upstream_name,
        "ahead": ahead,
        "behind": behind,
        "files": files,
        "truncated": truncated,
    }


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _hunk_id(path: str, header: str, index: int) -> str:
    digest = hashlib.sha1(f"{path}\0{index}\0{header}".encode("utf-8")).hexdigest()[:16]
    return f"{path}:{digest}"


def _parse_diff(patch: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None
    hunk_index = 0
    for line in patch.splitlines():
        m = _DIFF_HEADER_RE.match(line)
        if m:
            current = {"path": m.group(2), "oldPath": m.group(1), "hunks": []}
            files.append(current)
            current_hunk = None
            hunk_index = 0
            continue
        if current is None:
            continue
        hm = _HUNK_RE.match(line)
        if hm:
            hunk_index += 1
            current_hunk = {
                "id": _hunk_id(current["path"], line, hunk_index),
                "header": line,
                "oldStart": int(hm.group(1)),
                "oldLines": int(hm.group(2) or "1"),
                "newStart": int(hm.group(3)),
                "newLines": int(hm.group(4) or "1"),
                "lines": [],
            }
            current["hunks"].append(current_hunk)
            continue
        if current_hunk is not None:
            current_hunk["lines"].append(line)
    return files


def git_diff(workspace: str, path: str | None = None, staged: bool = False) -> dict[str, Any]:
    ctx = repo_context(workspace)
    args = ["diff", "--patch", "--find-renames", "--no-ext-diff", "--no-textconv"]
    if staged:
        args.append("--cached")
    if path:
        args.extend(["--", _repo_rel(ctx, path)])
    elif ctx.prefix:
        args.extend(["--", ctx.prefix])
    result = git_run(ctx.repo_root, args)
    patch = result.stdout
    truncated = False
    if len(patch.encode("utf-8", errors="replace")) > MAX_DIFF_BYTES:
        patch = patch.encode("utf-8", errors="replace")[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
        truncated = True
    files = _parse_diff(patch)
    for file_info in files:
        file_info["path"] = _to_workspace_rel(ctx, file_info.get("path"))
        file_info["oldPath"] = _to_workspace_rel(ctx, file_info.get("oldPath"))
    return {"ok": True, "workspace": ctx.workspace, "staged": staged, "patch": patch, "files": files, "truncated": truncated}


def git_stage(workspace: str, paths: list[str]) -> dict[str, Any]:
    ctx = repo_context(workspace)
    rels = _validate_paths(ctx, paths)
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        git_run(ctx.repo_root, ["add", "--", *rels])
    return {"ok": True, "paths": _to_workspace_paths(ctx, rels)}


def git_unstage(workspace: str, paths: list[str]) -> dict[str, Any]:
    ctx = repo_context(workspace)
    rels = _validate_paths(ctx, paths)
    with _mutation_lock(ctx.repo_root):
        result = git_run(ctx.repo_root, ["restore", "--staged", "--", *rels], check=False)
        if result.returncode != 0:
            git_run(ctx.repo_root, ["rm", "--cached", "-r", "--ignore-unmatch", "--", *rels], check=False)
    return {"ok": True, "paths": _to_workspace_paths(ctx, rels)}


def _has_unmerged(ctx: RepoContext, rels: list[str]) -> bool:
    result = git_run(ctx.repo_root, ["diff", "--name-only", "--diff-filter=U", "--", *rels], check=False)
    return bool(result.stdout.strip())


def _is_head_tracked(ctx: RepoContext, rel: str) -> bool:
    result = git_run(ctx.repo_root, ["ls-tree", "-r", "--name-only", "HEAD", "--", rel], check=False)
    return result.returncode == 0 and rel in result.stdout.splitlines()


def git_discard(workspace: str, paths: list[str], confirm_conflict: bool = False) -> dict[str, Any]:
    ctx = repo_context(workspace)
    rels = _validate_paths(ctx, paths)
    with _mutation_lock(ctx.repo_root):
        if _has_unmerged(ctx, rels) and not confirm_conflict:
            raise GitWorkspaceError("merge_conflict", "discarding conflicted files requires confirmation")
        tracked = []
        untracked = []
        for rel in rels:
            (tracked if _is_head_tracked(ctx, rel) else untracked).append(rel)
        if tracked:
            unstaged = git_run(ctx.repo_root, ["restore", "--staged", "--", *tracked], check=False)
            if unstaged.returncode != 0:
                git_run(ctx.repo_root, ["rm", "--cached", "-r", "--ignore-unmatch", "--", *tracked], check=False)
            head_tracked = [
                rel for rel in tracked
                if git_run(ctx.repo_root, ["ls-files", "--error-unmatch", "--", rel], check=False).returncode == 0
            ]
            if head_tracked:
                restored = git_run(ctx.repo_root, ["restore", "--worktree", "--", *head_tracked], check=False)
                if restored.returncode != 0:
                    raise GitWorkspaceError("git_failed", (restored.stderr or restored.stdout).strip())
            untracked.extend(rel for rel in tracked if rel not in head_tracked)
        for rel in untracked:
            git_run(ctx.repo_root, ["rm", "--cached", "-r", "--ignore-unmatch", "--", rel], check=False)
            abs_path = os.path.realpath(os.path.join(ctx.repo_root, rel))
            if os.path.exists(abs_path):
                if os.path.isfile(abs_path) or os.path.islink(abs_path):
                    os.remove(abs_path)
                elif os.path.isdir(abs_path):
                    shutil.rmtree(abs_path)
    return {"ok": True, "paths": _to_workspace_paths(ctx, rels)}


def _select_hunk_patch(workspace: str, path: str, hunk_id: str, *, staged: bool) -> str:
    diff = git_diff(workspace, path=path, staged=staged)
    patch_lines = diff["patch"].splitlines()
    for file_info in diff["files"]:
        for hunk in file_info["hunks"]:
            if hunk["id"] == hunk_id:
                ctx = repo_context(workspace)
                repo_old = f"{ctx.prefix}/{file_info['oldPath']}" if ctx.prefix and file_info["oldPath"] else file_info["oldPath"]
                repo_new = f"{ctx.prefix}/{file_info['path']}" if ctx.prefix and file_info["path"] else file_info["path"]
                start = next(i for i, line in enumerate(patch_lines) if line == f"diff --git a/{repo_old} b/{repo_new}")
                hunk_start = next(i for i in range(start, len(patch_lines)) if patch_lines[i] == hunk["header"])
                hunk_end = hunk_start + 1
                while hunk_end < len(patch_lines) and not patch_lines[hunk_end].startswith(("@@ ", "diff --git ")):
                    hunk_end += 1
                headers = patch_lines[start:hunk_start]
                return "\n".join(headers + patch_lines[hunk_start:hunk_end]) + "\n"
    raise GitWorkspaceError("git_failed", "hunk no longer exists")


def git_stage_hunk(workspace: str, path: str, hunk_id: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    _repo_rel(ctx, path)
    patch = _select_hunk_patch(workspace, path, hunk_id, staged=False)
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        if shutil.which("git") is None:
            raise GitWorkspaceError("missing_git", "git is not installed")
        try:
            result = subprocess.run(
                ["git", "apply", "--cached", "--unidiff-zero", "-"],
                cwd=ctx.repo_root,
                input=patch,
                text=True,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                env=_git_env(),
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitWorkspaceError("missing_git", "git is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitWorkspaceError("git_failed", "git command timed out") from exc
        if result.returncode != 0:
            raise GitWorkspaceError("git_failed", (result.stderr or result.stdout).strip())
    return {"ok": True, "path": path, "hunkId": hunk_id}


def git_unstage_hunk(workspace: str, path: str, hunk_id: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    _repo_rel(ctx, path)
    patch = _select_hunk_patch(workspace, path, hunk_id, staged=True)
    with _mutation_lock(ctx.repo_root):
        if shutil.which("git") is None:
            raise GitWorkspaceError("missing_git", "git is not installed")
        try:
            result = subprocess.run(
                ["git", "apply", "--cached", "--reverse", "--unidiff-zero", "-"],
                cwd=ctx.repo_root,
                input=patch,
                text=True,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                env=_git_env(),
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitWorkspaceError("missing_git", "git is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitWorkspaceError("git_failed", "git command timed out") from exc
        if result.returncode != 0:
            raise GitWorkspaceError("git_failed", (result.stderr or result.stdout).strip())
    return {"ok": True, "path": path, "hunkId": hunk_id}


def _ensure_message(message: str | None) -> str:
    msg = (message or "").strip()
    if not msg:
        raise GitWorkspaceError("git_failed", "commit message is required")
    return msg


def git_commit(workspace: str, message: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    with _mutation_lock(ctx.repo_root):
        if ctx.prefix:
            staged = git_run(ctx.repo_root, ["diff", "--cached", "--name-only"]).stdout.splitlines()
            prefix = ctx.prefix.rstrip("/") + "/"
            outside = [path for path in staged if path != ctx.prefix and not path.startswith(prefix)]
            if outside:
                raise GitWorkspaceError("outside_workspace", "staged changes outside the workspace cannot be committed")
        git_run(ctx.repo_root, ["commit", "-m", _ensure_message(message)])
        sha = git_run(ctx.repo_root, ["rev-parse", "HEAD"]).stdout.strip()
    return {"ok": True, "commit": sha}


def git_commit_selected(workspace: str, paths: list[str], message: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    rels = _validate_paths(ctx, paths)
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        index_path = git_run(ctx.repo_root, ["rev-parse", "--git-path", "index"]).stdout.strip()
        if not os.path.isabs(index_path):
            index_path = os.path.join(ctx.repo_root, index_path)
        with tempfile.NamedTemporaryFile(prefix="odysseus-git-index-", delete=False) as tmp:
            tmp_index = tmp.name
        try:
            if os.path.exists(index_path):
                shutil.copy2(index_path, tmp_index)
            else:
                os.remove(tmp_index)
            env = {"GIT_INDEX_FILE": tmp_index}
            git_run(ctx.repo_root, ["add", "-A", "--", *rels], env=env)
            git_run(ctx.repo_root, ["commit", "-m", _ensure_message(message), "--", *rels], env=env)
            sha = git_run(ctx.repo_root, ["rev-parse", "HEAD"]).stdout.strip()
            git_run(ctx.repo_root, ["reset", "-q", "HEAD", "--", *rels])
        finally:
            try:
                os.remove(tmp_index)
            except OSError:
                pass
    return {"ok": True, "commit": sha, "paths": _to_workspace_paths(ctx, rels)}


def git_branches(workspace: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    current = git_run(ctx.repo_root, ["branch", "--show-current"], check=False).stdout.strip()
    local = []
    lines = git_run(ctx.repo_root, ["for-each-ref", "--format=%(refname:short)|%(upstream:short)", "refs/heads"]).stdout.splitlines()
    for line in lines:
        name, _, upstream = line.partition("|")
        ahead = behind = 0
        if upstream:
            counts = git_run(ctx.repo_root, ["rev-list", "--left-right", "--count", f"{upstream}...{name}"], check=False)
            if counts.returncode == 0 and len(counts.stdout.split()) == 2:
                behind, ahead = map(int, counts.stdout.split())
        local.append({"name": name, "current": name == current, "upstream": upstream or None, "ahead": ahead, "behind": behind})
    remotes = [{"name": line.strip()} for line in git_run(ctx.repo_root, ["branch", "-r", "--format=%(refname:short)"], check=False).stdout.splitlines() if line.strip()]
    return {"ok": True, "current": current, "local": local, "remote": remotes}


def _is_dirty(ctx: RepoContext) -> bool:
    return bool(git_run(ctx.repo_root, ["status", "--porcelain"], check=False).stdout.strip())


# Auto-stashes created during branch switches are tagged with this prefix so we
# only ever pop our own (never the user's manual `git stash`). The stash's git
# subject is "On <source-branch>: odysseus-branch-switch ...", which lets us key
# a parked change-set to the branch it was made on and restore it on return.
_AUTOSTASH_PREFIX = "odysseus-branch-switch"


def _autostash_entries(repo_root: str) -> list[dict[str, str]]:
    """Our branch-switch stashes, each as {ref, branch} where branch is the
    source branch the stash was created on (parsed from the 'On <branch>:' subject)."""
    result = git_run(repo_root, ["stash", "list", "--format=%gd%x00%gs"], check=False)
    if result.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        ref, sep, subject = line.partition("\x00")
        if not sep or not subject.startswith("On "):
            continue
        head, sep2, message = subject[3:].partition(": ")
        if not sep2 or not message.startswith(_AUTOSTASH_PREFIX):
            continue
        entries.append({"ref": ref, "branch": head.strip()})
    return entries


def _restore_autostash(repo_root: str, branch: str) -> dict[str, bool]:
    """Pop the parked change-set for `branch` (the branch just checked out), if
    one exists and the worktree is clean. Only touches our tagged stashes."""
    if bool(git_run(repo_root, ["status", "--porcelain"], check=False).stdout.strip()):
        return {"restored": False}
    for item in _autostash_entries(repo_root):
        if item["branch"] != branch:
            continue
        result = git_run(repo_root, ["stash", "pop", "--index", item["ref"]], check=False)
        if result.returncode == 0:
            return {"restored": True}
        # Conflict on restore: leave the stash in place for manual recovery.
        return {"restored": False, "restore_failed": True}
    return {"restored": False}


def git_checkout(workspace: str, branch: str, *, stash: bool = False) -> dict[str, Any]:
    ctx = repo_context(workspace)
    _require_repo_root_workspace(ctx)
    branch = (branch or "").strip()
    if not branch:
        raise GitWorkspaceError("git_failed", "branch is required")
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        stashed = False
        if _is_dirty(ctx):
            if not stash:
                raise GitWorkspaceError("dirty_worktree", "worktree has uncommitted changes")
            # Park dirty changes tagged to the CURRENT branch so returning here
            # later restores them (per-branch auto-stash).
            current = git_run(ctx.repo_root, ["branch", "--show-current"], check=False).stdout.strip()
            result = git_run(ctx.repo_root, ["stash", "push", "-u", "-m", f"{_AUTOSTASH_PREFIX} to {branch}"])
            stashed = "No local changes to save" not in (result.stdout + result.stderr)
            try:
                git_run(ctx.repo_root, ["checkout", branch])
            except GitWorkspaceError:
                # Roll back the stash so the user's changes are never stranded.
                still_here = git_run(ctx.repo_root, ["branch", "--show-current"], check=False).stdout.strip()
                if stashed and current == still_here:
                    git_run(ctx.repo_root, ["stash", "pop", "--index", "stash@{0}"], check=False)
                raise
        else:
            git_run(ctx.repo_root, ["checkout", branch])
        # Whether we arrived clean or after parking, restore this branch's
        # previously-parked changes if any.
        restored = _restore_autostash(ctx.repo_root, branch)
    return {
        "ok": True,
        "branch": branch,
        "stashed": stashed,
        "restored": bool(restored.get("restored")),
        "restoreFailed": bool(restored.get("restore_failed")),
    }


def git_create_branch(workspace: str, branch: str) -> dict[str, Any]:
    """Create a new branch and switch to it. `git checkout -b` carries any
    uncommitted changes onto the new branch (normal git behaviour), so no stash
    is needed here. Rejects unsafe/duplicate names with a clean error."""
    ctx = repo_context(workspace)
    _require_repo_root_workspace(ctx)
    branch = (branch or "").strip()
    if not branch:
        raise GitWorkspaceError("git_failed", "branch name is required")
    # Reject leading '-' (option injection) before any name reaches git.
    if branch.startswith("-"):
        raise GitWorkspaceError("git_failed", f"invalid branch name: {branch}")
    if git_run(ctx.repo_root, ["check-ref-format", "--branch", branch], check=False).returncode != 0:
        raise GitWorkspaceError("git_failed", f"invalid branch name: {branch}")
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        exists = git_run(ctx.repo_root, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
        if exists.returncode == 0:
            raise GitWorkspaceError("git_failed", f"branch already exists: {branch}")
        git_run(ctx.repo_root, ["checkout", "-b", branch])
    return {"ok": True, "branch": branch, "created": True}


def git_remote_action(workspace: str, action: str) -> dict[str, Any]:
    if action not in {"fetch", "pull", "push"}:
        raise GitWorkspaceError("git_failed", "unknown remote action")
    ctx = repo_context(workspace)
    _require_repo_root_workspace(ctx)
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        result = git_run(ctx.repo_root, [action], check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            code = "merge_conflict" if "conflict" in message.lower() else "git_failed"
            raise GitWorkspaceError(code, message)
    return {"ok": True, "output": (result.stdout or result.stderr).strip()}


def git_init(workspace: str) -> dict[str, Any]:
    ws = resolve_workspace(workspace)
    with _mutation_lock(ws):
        git_run(ws, ["init"])
    return {"ok": True, "workspace": ws}


def _inside_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text(errors="ignore") or "kubepods" in Path("/proc/1/cgroup").read_text(errors="ignore")
    except OSError:
        return False


def default_clone_parent() -> str:
    return "/app/data/workspaces" if _inside_docker() else os.path.expanduser("~/odysseus-workspaces")


def git_clone(workspace: str | None, url: str, target: str | None = None, name: str | None = None) -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise GitWorkspaceError("git_failed", "clone url is required")
    if name and (os.path.basename(name) != name or name in {".", ".."}):
        raise GitWorkspaceError("outside_workspace", "unsafe target name")
    if target:
        if not workspace:
            raise GitWorkspaceError("invalid_workspace", "workspace is required when target is supplied")
        parent = resolve_workspace_path(resolve_workspace(workspace), target, must_exist=False, allow_absolute=True)
    else:
        parent = os.path.realpath(default_clone_parent())
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise GitWorkspaceError("git_failed", str(exc)) from exc
    inferred_name = os.path.splitext(os.path.basename(url.rstrip("/")))[0] or "repository"
    if not name and (os.path.basename(inferred_name) != inferred_name or inferred_name in {".", ".."}):
        raise GitWorkspaceError("outside_workspace", "unsafe target name")
    dest = os.path.join(parent, name or inferred_name)
    dest = os.path.realpath(dest)
    try:
        if os.path.commonpath([_norm(dest), _norm(parent)]) != _norm(parent):
            raise ValueError
    except ValueError as exc:
        raise GitWorkspaceError("outside_workspace", "clone target is outside parent") from exc
    try:
        with _mutation_lock(parent):
            git_run(parent, ["clone", url, dest])
    except OSError as exc:
        raise GitWorkspaceError("git_failed", str(exc)) from exc
    return {"ok": True, "path": os.path.realpath(dest)}


def _git_remotes(ctx: RepoContext) -> set[str]:
    result = git_run(ctx.repo_root, ["remote"], check=False)
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _parse_refs(decoration: str, remotes: set[str]) -> list[dict[str, Any]]:
    """Turn a `--decorate=short` `%D` string into typed ref objects so the
    frontend never has to string-parse decorations. Ref names cannot contain a
    space, so splitting on ", " is unambiguous."""
    refs: list[dict[str, Any]] = []
    for raw in (decoration or "").strip().split(", "):
        name = raw.strip()
        if not name:
            continue
        if name.startswith("HEAD -> "):
            refs.append({"name": name[len("HEAD -> "):].strip(), "type": "head", "current": True})
        elif name == "HEAD":
            refs.append({"name": "HEAD", "type": "head", "current": True})
        elif name.startswith("tag: "):
            refs.append({"name": name[len("tag: "):].strip(), "type": "tag", "current": False})
        else:
            remote = name.split("/", 1)[0] in remotes
            refs.append({"name": name, "type": "remote" if remote else "local", "current": False})
    return refs


def git_history(workspace: str, path: str | None = None, limit: int = 50) -> dict[str, Any]:
    ctx = repo_context(workspace)
    limit = max(1, min(int(limit or 50), 200))
    fmt = "%H%x1f%P%x1f%D%x1f%an%x1f%ae%x1f%ad%x1f%s"
    args = ["log", f"-n{limit}", "--date-order", "--decorate=short", "--date=iso-strict", f"--pretty=format:{fmt}"]
    if path:
        # File scope stays linear on the current branch (single file).
        args.extend(["--", _repo_rel(ctx, path)])
    else:
        # Repo scope draws every local + remote ref as a lane.
        args.append("--all")
        if ctx.prefix:
            args.extend(["--", ctx.prefix])
    result = git_run(ctx.repo_root, args)
    remotes = _git_remotes(ctx)
    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 7:
            commits.append({
                "sha": parts[0],
                "parents": parts[1].split(),
                "refs": _parse_refs(parts[2], remotes),
                "author": parts[3],
                "email": parts[4],
                "date": parts[5],
                "message": parts[6],
            })
    return {"ok": True, "commits": commits}


_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{4,40}")


def _numstat_count(value: str) -> int | None:
    """A numstat column is a decimal count, or '-' for a binary file."""
    return int(value) if value.isdigit() else None


def git_commit_stat(workspace: str, sha: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    candidate = (sha or "").strip()
    if not _COMMIT_SHA_RE.fullmatch(candidate):
        raise GitWorkspaceError("git_failed", "invalid commit id")
    verify = git_run(ctx.repo_root, ["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"], check=False)
    resolved = verify.stdout.strip()
    if verify.returncode != 0 or not resolved:
        raise GitWorkspaceError("git_failed", "unknown commit")
    # An RS (\x1e) terminates the header so a multi-line body never collides with
    # the numstat block that follows; fields inside the header split on US (\x1f).
    fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%ad%x1f%s%x1f%b%x1e"
    result = git_run(ctx.repo_root, ["show", "--no-color", "--numstat", f"--format={fmt}", resolved, "--"])
    header, _, rest = result.stdout.partition("\x1e")
    fields = header.split("\x1f")
    if len(fields) != 7:
        raise GitWorkspaceError("git_failed", "could not read commit")
    files: list[dict[str, Any]] = []
    additions = 0
    deletions = 0
    for line in rest.splitlines():
        if "\t" not in line:
            continue
        ins_raw, del_raw, path = line.split("\t", 2)
        insertions = _numstat_count(ins_raw)
        removed = _numstat_count(del_raw)
        additions += insertions or 0
        deletions += removed or 0
        files.append({"path": path, "insertions": insertions, "deletions": removed})
    return {
        "ok": True,
        "commit": {
            "sha": fields[0],
            "parents": fields[1].split(),
            "author": fields[2],
            "email": fields[3],
            "date": fields[4],
            "subject": fields[5],
            "body": fields[6].rstrip("\n"),
            "files": files,
            "fileCount": len(files),
            "additions": additions,
            "deletions": deletions,
        },
    }


def git_blame(workspace: str, path: str) -> dict[str, Any]:
    file_info = read_workspace_file(workspace, path)
    if file_info.get("binary"):
        raise GitWorkspaceError("binary_file", "blame is only available for text files")
    if int(file_info.get("size") or 0) > MAX_BLAME_BYTES:
        raise GitWorkspaceError("git_failed", "file is too large to blame")
    ctx = repo_context(workspace)
    rel = _repo_rel(ctx, path)
    result = git_run(ctx.repo_root, ["blame", "--line-porcelain", "--", rel])
    lines = []
    current: dict[str, Any] = {}
    for line in result.stdout.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            current = {"sha": line.split()[0]}
        elif line.startswith("author "):
            current["author"] = line[7:]
        elif line.startswith("\t"):
            current["text"] = line[1:]
            lines.append(current)
    return {"ok": True, "path": _to_workspace_rel(ctx, rel), "lines": lines}


# Match a real git conflict-marker line: exactly seven marker characters at the
# start of a line, optionally followed by a label (e.g. "<<<<<<< HEAD"). Anchoring
# and the exact-7 length keep legitimate content from false-positiving — an 8+ char
# "========" underline or a long "====" rule is not a marker — while still catching
# every marker git emits. The "|||||||" alternative is the diff3/zdiff3 base
# separator, so resolutions in that conflict style cannot slip a leaked base
# section past validation and get staged.
_CONFLICT_MARKER_RE = re.compile(
    r"^(?:<{7}|\|{7}|={7}|>{7})(?:[ \t].*)?$",
    re.MULTILINE,
)


def _contains_conflict_markers(text: str) -> bool:
    return bool(_CONFLICT_MARKER_RE.search(text))


def git_conflicts(workspace: str) -> dict[str, Any]:
    ctx = repo_context(workspace)
    args = ["diff", "--name-only", "--diff-filter=U"]
    if ctx.prefix:
        args.extend(["--", ctx.prefix])
    result = git_run(ctx.repo_root, args, check=False)
    paths = set(_to_workspace_rel(ctx, p) for p in result.stdout.splitlines() if p.strip())
    scanned = 0
    truncated = False
    for root, dirs, files in os.walk(ctx.workspace):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS and not d.startswith(".")]
        for filename in files:
            if scanned >= MAX_CONFLICT_SCAN_FILES:
                truncated = True
                break
            scanned += 1
            abs_path = os.path.join(root, filename)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    if _contains_conflict_markers(fh.read(TEXT_PREVIEW_LIMIT)):
                        paths.add(workspace_rel(ctx.workspace, abs_path))
            except OSError:
                continue
        if truncated:
            break
    return {"ok": True, "files": [{"path": p} for p in sorted(paths)], "truncated": truncated}


def git_resolve_conflict(workspace: str, path: str, content: str) -> dict[str, Any]:
    if _contains_conflict_markers(content):
        raise GitWorkspaceError("merge_conflict", "conflict markers remain")
    saved = save_workspace_file(workspace, path, content)
    ctx = repo_context(workspace)
    rel = _repo_rel(ctx, path)
    with _mutation_lock(ctx.repo_root):
        _reject_unsafe_git_config(ctx.repo_root)
        git_run(ctx.repo_root, ["add", "--", rel])
    return {"ok": True, "path": saved["path"]}
