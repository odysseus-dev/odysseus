"""
tool_execution.py

Tool dispatcher and result formatter for the agent loop.
Routes tool blocks to MCP servers or native implementations.

Extracted from agent_tools.py.
"""

import asyncio
import collections
import json
import logging
import os
import pathlib
import re
import sys
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.tool_security import is_public_blocked_tool, owner_is_admin_or_single_user
from src.tool_policy import ToolPolicy
from src.constants import MAX_OUTPUT_CHARS, MAX_READ_CHARS, MAX_DIFF_LINES

# Persistent working directory for agent subprocesses.
# Resolves to <repo_root>/data, which is the bind-mounted volume in Docker
# (/app/data) and the local data directory for manual installs.
# Using this as cwd and HOME prevents the agent from silently creating files
# in ephemeral container layers that are lost on the next rebuild.
_AGENT_WORKDIR = str(pathlib.Path(__file__).parent.parent / "data")


def _unified_diff(old: str, new: str, path: str) -> Optional[Dict[str, Any]]:
    """Build a unified diff of a file write for display in the chat.

    Returns {"text": <unified diff>, "added": N, "removed": M, "new_file": bool}
    or None when there's no textual change. Truncates very large diffs.
    """
    if old == new:
        return None
    import difflib

    old_lines = old.splitlines()
    new_lines = new.splitlines()
    label = path or "file"
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{label}", tofile=f"b/{label}",
        lineterm="",
    ))
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    truncated = False
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_lines = diff_lines[:MAX_DIFF_LINES]
        truncated = True
    text = "\n".join(diff_lines)
    if truncated:
        text += f"\n… diff truncated at {MAX_DIFF_LINES} lines"
    return {
        "text": text,
        "added": added,
        "removed": removed,
        "new_file": old == "",
        "file": os.path.basename(path) or (path or "file"),
    }


async def _do_edit_file(content: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Exact string-replacement edit of an on-disk file.

    content is JSON: {"path", "old_string", "new_string", "replace_all"?}.
    Fails if old_string is missing or non-unique (unless replace_all) so the
    model can't silently edit the wrong place. Returns a unified diff for the UI.
    Confined to the workspace when one is set (same policy as write_file).
    """
    try:
        args = json.loads(content) if content.strip().startswith("{") else {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    raw_path = (args.get("path") or "").strip()
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if not raw_path:
        return {"error": "edit_file: path required", "exit_code": 1}
    # Confine to the workspace when set, else the same allowlist + sensitive-file
    # policy as read/write_file.
    try:
        path = (_resolve_tool_path_in_workspace(workspace, raw_path)
                if workspace else _resolve_tool_path(raw_path))
    except ValueError as e:
        return {"error": f"edit_file: {e}", "exit_code": 1}
    if old == "":
        return {"error": "edit_file: old_string required (use write_file to create a file)", "exit_code": 1}
    if old == new:
        return {"error": "edit_file: old_string and new_string are identical", "exit_code": 1}

    def _apply():
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
        count = original.count(old)
        if count == 0:
            return original, None, "not_found"
        if count > 1 and not replace_all:
            return original, None, f"not_unique:{count}"
        updated = original.replace(old, new) if replace_all else original.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        return original, updated, "ok"

    try:
        original, updated, status = await asyncio.to_thread(_apply)
    except FileNotFoundError:
        return {"error": f"edit_file: {path}: not found (use write_file to create it)", "exit_code": 1}
    except (IsADirectoryError, UnicodeDecodeError):
        return {"error": f"edit_file: {path}: not an editable text file", "exit_code": 1}
    except PermissionError:
        return {"error": f"edit_file: {path}: permission denied", "exit_code": 1}
    except OSError as e:
        return {"error": f"edit_file: {path}: {e}", "exit_code": 1}

    if status == "not_found":
        return {"error": f"edit_file: old_string not found in {path}. Read the file and match it exactly.", "exit_code": 1}
    if status.startswith("not_unique"):
        n = status.split(":", 1)[1]
        return {"error": f"edit_file: old_string is not unique in {path} ({n} matches). Add surrounding context or set replace_all=true.", "exit_code": 1}

    n = original.count(old)
    result = {"output": f"Edited {path} ({n} replacement{'s' if n != 1 else ''})", "exit_code": 0}
    diff = _unified_diff(original, updated, path)
    if diff:
        result["diff"] = diff
    return result

# ---------------------------------------------------------------------------
# Path confinement for read_file / write_file
# ---------------------------------------------------------------------------
# read_file + write_file are admin-only tools, but the path the agent
# supplies is model-controlled. Prompt-injection in an admin's chat can
# weaponise "read /etc/shadow" or "write ~/.ssh/authorized_keys" without
# the admin noticing.
#
# Policy:
#   1. Sensitive-subpath deny list — checked FIRST. Blocks .ssh,
#      .gnupg, shell rc files, token/env files even if the root above
#      them is on the allowlist.
#   2. Allowlist — only the directories the agent legitimately needs
#      (project data/, system tmp). $HOME is NOT on the default list.
#   3. Opt-in extra roots — admin can add broader roots via the
#      "tool_path_extra_roots" setting (list of path strings).
# ---------------------------------------------------------------------------

_SENSITIVE_BASENAMES: set[str] = {
    ".ssh", ".gnupg", ".gitconfig",
    ".bashrc", ".bash_profile", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv",
    ".profile", ".tcshrc", ".cshrc",
    ".env", ".netrc",
}

_SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa",
    "known_hosts",
)


def _is_sensitive_path(resolved: str) -> bool:
    """Return True if *resolved* falls under a sensitive directory or
    matches a sensitive filename — regardless of what root it sits under.
    """
    parts = resolved.split(os.sep)
    filenames: set[str] = {parts[-1]} if parts else set()

    # Check if any path component is a sensitive directory.
    for part in parts:
        if part in _SENSITIVE_BASENAMES:
            return True

    # Check filename against known sensitive files.
    for pat in _SENSITIVE_FILE_PATTERNS:
        if pat in filenames:
            return True

    return False


def _tool_path_roots() -> list[str]:
    """Return the list of directory roots that read_file / write_file
    may touch. Default: project data/ + system temp dirs. Extra roots
    are loaded from the ``tool_path_extra_roots`` setting.
    """
    roots: list[str] = []

    # Project data directory — the agent's primary workspace.
    from src.constants import DATA_DIR
    roots.append(DATA_DIR)

    # /tmp (and its macOS realpath /private/tmp).
    roots.append("/tmp")
    try:
        private_tmp = os.path.realpath("/tmp")
        if private_tmp != "/tmp":
            roots.append(private_tmp)
    except OSError:
        pass

    # $TMPDIR — per-user temp root on macOS (e.g. /var/folders/.../T/).
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)

    # Opt-in extra roots from settings.
    try:
        from src.settings import get_setting
        extra = get_setting("tool_path_extra_roots")
        if isinstance(extra, list):
            roots.extend(str(r) for r in extra if r)
    except Exception:
        pass

    # Deduplicate; resolve symlinks so containment is unambiguous.
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        try:
            real = os.path.realpath(r)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        out.append(real)
    return out


def _resolve_tool_path(raw_path: str) -> str:
    """Resolve and confine a model-supplied path.

    Order of checks:
      1. Non-empty path.
      2. Sensitive-subpath deny list (blocks .ssh, .gnupg, etc.
         even when the root is on the allowlist).
      3. Allowlist containment (must land under one of the roots).

    Returns the realpath on success. Raises ValueError on rejection.
    Symlinks are resolved before comparison.
    """
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    expanded = os.path.expanduser(str(raw_path).strip())
    resolved = os.path.realpath(expanded)

    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )

    for root in _tool_path_roots():
        if resolved == root:
            return resolved
        try:
            common = os.path.commonpath([resolved, root])
        except ValueError:
            continue
        if common == root:
            return resolved
    raise ValueError(
        f"path '{raw_path}' is outside the allowed roots"
    )


def _resolve_tool_path_in_workspace(workspace: str, raw_path: str) -> str:
    """Confine a model-supplied path to the active workspace.

    Layered on top of upstream's path policy: the workspace is the allowed
    root (relative paths resolve under it; paths that escape it are rejected),
    and the sensitive-file deny list (.ssh, .gnupg, id_rsa, …) still applies
    inside it. When no workspace is set, callers use _resolve_tool_path (the
    default data/tmp allowlist) instead.
    """
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    base = os.path.realpath(workspace)
    expanded = os.path.expanduser(str(raw_path).strip())
    candidate = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
    resolved = os.path.realpath(candidate)
    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )
    if resolved != base:
        # normcase so containment holds on case-insensitive filesystems
        # (Windows, default macOS): it lowercases on Windows and is a no-op on
        # POSIX. commonpath raises ValueError across Windows drives (C: vs D:)
        # or mixed abs/rel — both mean "outside", so the except rejects them.
        nbase = os.path.normcase(base)
        try:
            if os.path.commonpath([os.path.normcase(resolved), nbase]) != nbase:
                raise ValueError
        except ValueError:
            raise ValueError(f"path '{raw_path}' is outside the workspace ({workspace})")
    return resolved

# Bash + python tools used to share a single 60s timeout. That's
# enough for one-shot commands but starves real workloads (pip
# install, ffmpeg conversions, etc.) — and worse, the agent saw the
# 60s timeout and went silent because it had nothing to report.
# The new default is intentionally generous: long enough that real
# work isn't killed mid-flight, but bounded so a runaway process
# (infinite loop, hung connect, etc.) eventually frees the worker.
# The user can cancel sooner via the chat stop button — when the
# SSE stream is torn down, the asyncio task running the subprocess
# gets cancelled and the subprocess is killed by the finally block.
DEFAULT_BASH_TIMEOUT = 60 * 60     # 1 hour
DEFAULT_PYTHON_TIMEOUT = 60 * 60

# How often to push a progress event while a long-running subprocess
# is still in flight. The frontend cares about "alive" more than
# "every-byte" — 2s is the sweet spot.
PROGRESS_INTERVAL_S = 2.0
# Tail buffer size — we keep the most recent N lines of stdout +
# stderr so the progress event includes a "what's it doing right now"
# snippet without dragging the whole output along.
PROGRESS_TAIL_LINES = 12


def get_mcp_manager():
    from src import agent_tools
    return agent_tools.get_mcp_manager()


# Directories ignored by the code-nav tools' Python fallbacks so results aren't
# polluted by VCS internals / dependency trees / build caches. ripgrep already
# honours .gitignore; this is the parity floor for the no-rg path (and the
# explicit excludes passed to rg so it skips them even without a .gitignore).
_CODENAV_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".cache", "site-packages", ".idea", ".tox",
})
# Per-tool result caps (keep tool output cheap + model-friendly).
_CODENAV_MAX_HITS = 200
_CODENAV_MAX_LINE = 400


def _resolve_search_root(raw_path: str, workspace: Optional[str] = None) -> str:
    """Resolve + confine a code-nav path (grep/glob/ls).

    With a workspace set, the workspace folder is the root and supplied paths are
    confined inside it (same policy as read_file). Without one, an empty path
    defaults to the agent's primary root (project data dir) and a supplied path
    is confined by the global allowlist + sensitive-file policy.
    """
    raw = (raw_path or "").strip()
    if workspace:
        if not raw:
            return os.path.realpath(workspace)
        return _resolve_tool_path_in_workspace(workspace, raw)
    if not raw:
        roots = _tool_path_roots()
        return roots[0] if roots else os.path.realpath(".")
    return _resolve_tool_path(raw)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text

logger = logging.getLogger(__name__)


async def _run_subprocess_streaming(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    """Run a subprocess to completion, streaming progress.

    Reads stdout + stderr line-by-line into ring buffers so a
    periodic progress callback can emit a "tail" of recent output
    without waiting for the full result. Returns
    (full_stdout, full_stderr, return_code, timed_out).

    `timed_out=True` means the process was killed because it ran
    past `timeout` seconds. Whatever output we'd buffered up to
    that point is still returned.
    """
    started = time.time()
    stdout_full: list[str] = []
    stderr_full: list[str] = []
    tail = collections.deque(maxlen=PROGRESS_TAIL_LINES)

    async def _reader(stream, full_buf, label: str):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
            full_buf.append(decoded)
            if label == "err":
                tail.append(f"! {decoded}")
            else:
                tail.append(decoded)

    async def _progress_emitter():
        # Skip the first push — many commands finish well under
        # PROGRESS_INTERVAL_S and a 0-second "progress" event would
        # just add UI churn.
        await asyncio.sleep(PROGRESS_INTERVAL_S)
        while True:
            if progress_cb:
                try:
                    await progress_cb({
                        "elapsed_s": round(time.time() - started, 1),
                        "tail": "\n".join(list(tail)),
                    })
                except Exception:
                    # Progress is best-effort — never let a UI hiccup
                    # break the underlying subprocess.
                    pass
            await asyncio.sleep(PROGRESS_INTERVAL_S)

    rd_out = asyncio.create_task(_reader(proc.stdout, stdout_full, "out"))
    rd_err = asyncio.create_task(_reader(proc.stderr, stderr_full, "err"))
    prog_task = asyncio.create_task(_progress_emitter()) if progress_cb else None

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
    except asyncio.CancelledError:
        # User hit stop / SSE stream torn down. Kill the child so it
        # doesn't keep running orphaned. Re-raise so the agent loop's
        # cancellation propagates as the user expects.
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        # Best-effort: stop the readers + emitter before re-raising.
        for t in (rd_out, rd_err):
            t.cancel()
        if prog_task is not None:
            prog_task.cancel()
        raise
    finally:
        if prog_task is not None and not prog_task.done():
            prog_task.cancel()
            try:
                await prog_task
            except (asyncio.CancelledError, Exception):
                pass
        # Wait for readers to finish draining the pipes.
        for t in (rd_out, rd_err):
            try:
                await asyncio.wait_for(t, timeout=1)
            except Exception:
                pass

    return (
        "\n".join(stdout_full),
        "\n".join(stderr_full),
        proc.returncode,
        timed_out,
    )

_ADMIN_TOOLS = {
    "app_api",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_settings",
    "download_model",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "cancel_download",
}


def _owner_is_admin(owner: Optional[str]) -> bool:
    """Mirror route-level admin behavior for agent tool execution."""
    return owner_is_admin_or_single_user(owner)

# ---------------------------------------------------------------------------
# MCP-backed tool helpers
# ---------------------------------------------------------------------------

# Map legacy tool names -> (MCP server_id, MCP tool_name)
_MCP_TOOL_MAP = {
    "bash":           ("bash",       "bash"),
    "python":         ("python",     "python"),
    "read_file":      ("filesystem", "read_file"),
    "write_file":     ("filesystem", "write_file"),
    "web_search":     ("web_search", "web_search"),
    "web_fetch":      ("web_fetch",  "web_fetch"),
    "generate_image": ("image_gen",  "generate_image"),
}


def _parse_generate_image(content: str) -> Dict:
    lines = content.strip().split("\n")
    args = {"prompt": lines[0].strip() if lines else ""}
    for i, key in enumerate(["model", "size", "quality"], 1):
        if len(lines) > i and lines[i].strip():
            args[key] = lines[i].strip()
    return args


def _parse_manage_memory(content: str) -> Dict:
    lines = content.strip().split("\n")
    action = lines[0].strip().lower() if lines else ""
    args = {"action": action}
    if action == "add":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2 and lines[2].strip():
            args["category"] = lines[2].strip().lower()
    elif action == "edit":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
        args["text"] = lines[2].strip() if len(lines) > 2 else ""
    elif action == "delete":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "search":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "list":
        if len(lines) > 1 and lines[1].strip():
            args["category"] = lines[1].strip().lower()
    return args


def _parse_write_file(content: str) -> Dict:
    lines = content.split("\n", 1)
    return {"path": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""}


_MCP_ARG_PARSERS: Dict[str, Callable[[str], Dict[str, str]]] = {
    "bash":           lambda c: {"command": c},
    "python":         lambda c: {"code": c},
    "web_search":     lambda c: {"query": c.split("\n")[0].strip()},
    "web_fetch":      lambda c: {"url": c.split("\n")[0].strip()},
    "read_file":      lambda c: {"path": c.split("\n")[0].strip()},
    "write_file":     _parse_write_file,
    "generate_image": _parse_generate_image,
    "manage_memory":  _parse_manage_memory,
}


def _build_mcp_args(tool: str, content: str) -> Dict:
    """Convert fenced-block text content to structured MCP arguments."""
    parser = _MCP_ARG_PARSERS.get(tool)
    return parser(content) if parser else {}


async def _call_mcp_tool(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    workspace: Optional[str] = None,
) -> Dict:
    """Route a legacy tool call through the MCP manager, with direct fallbacks."""
    mcp = get_mcp_manager()
    if not mcp:
        return await _direct_fallback(tool, content, progress_cb=progress_cb, workspace=workspace) or {"error": f"MCP manager not available for tool '{tool}'", "exit_code": 1}

    server_id, tool_name = _MCP_TOOL_MAP[tool]
    qualified = f"mcp__{server_id}__{tool_name}"
    args = _build_mcp_args(tool, content)
    result = await mcp.call_tool(qualified, args)

    # If MCP server not connected, try direct fallback
    if isinstance(result, dict) and result.get("exit_code") == 1 and "not connected" in result.get("error", ""):
        fallback = await _direct_fallback(tool, content, progress_cb=progress_cb, workspace=workspace)
        if fallback:
            return fallback

    # generate_image runs as a text-only MCP tool, so the saved image URL never
    # reaches the agent loop's structured forwarding (which renders the image via
    # buildImageBubble on result["image_url"]). Lift it out of the tool's stdout so
    # the image renders deterministically — no dependence on the model echoing the
    # URL into its prose (which it mangles/hallucinates).
    if tool == "generate_image":
        _promote_image_fields(result)

    return result


def _promote_image_fields(result: Dict) -> None:
    """Lift the image URL (+ prompt/model/size) from a successful generate_image MCP
    text result into structured fields the agent loop already forwards to
    buildImageBubble. Only acts on a dict result with exit_code 0; matches the
    generated-image URL by pattern (absolute or relative) so it's robust to the
    result's wording."""
    if not isinstance(result, dict) or result.get("exit_code") != 0:
        return
    out = result.get("stdout") or ""
    m = re.search(r'(?:https?://[^\s)\]]+)?/api/generated-image/[A-Za-z0-9._-]+', out)
    if not m:
        return
    result["image_url"] = m.group(0).strip()
    for field, pat in (
        ("image_prompt", r'^Generated image for:\s*(.+)$'),
        ("image_model", r'^model:\s*(.+)$'),
        ("image_size", r'^size:\s*(.+)$'),
    ):
        fm = re.search(pat, out, re.M)
        if fm:
            result[field] = fm.group(1).strip()


_BG_MARKERS = {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}


def _split_bg_marker(content: str):
    """If the bash content's first non-empty line is a background marker
    (e.g. `#!bg`), return (True, command_without_marker); else (False, content)."""
    lines = content.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower() in _BG_MARKERS:
        del lines[i]
        return True, "\n".join(lines).strip()
    return False, content


async def _direct_fallback(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    workspace: Optional[str] = None,
) -> Optional[Dict]:
    """In-process execution path for the eight tools that used to live as
    stdio MCP servers under mcp_servers/. Those servers were deleted in
    favor of native execution; this function is now the canonical path,
    not a fallback. The name is kept for backwards compat with callers.

    `progress_cb` is called periodically while bash/python subprocesses
    are still running, with `{elapsed_s, tail}` payloads. Other tools
    ignore it.
    """
    # Inherit env + force a sane terminal so subprocesses that touch
    # terminfo (anything calling `clear`, `tput`, `os.system("clear")`,
    # or scripts that probe $TERM) don't spam "TERM environment variable
    # not set" errors. The agent's bash/python tool calls run with PIPE
    # stdin/stdout (no real TTY), so curses/termios still won't work —
    # but at least non-interactive code with incidental TERM lookups
    # stops failing. COLUMNS/LINES give terminal-width-aware tools (less,
    # rich, etc.) reasonable defaults instead of 0×0.
    _subproc_env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLUMNS": "120",
        "LINES": "40",
        "HOME": _AGENT_WORKDIR,
    }

    try:
        if tool == "bash":
            proc = await asyncio.create_subprocess_shell(
                content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
                cwd=workspace or _AGENT_WORKDIR,
            )
            stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
                proc,
                timeout=DEFAULT_BASH_TIMEOUT,
                progress_cb=progress_cb,
            )
            if timed_out:
                return {"error": f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(stdout, MAX_OUTPUT_CHARS), "stderr": _truncate(stderr, MAX_OUTPUT_CHARS)}
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
            output = _truncate(output, MAX_OUTPUT_CHARS)
            return {"output": output or "(no output)", "exit_code": rc or 0}

        if tool == "python":
            # Run user code in a subprocess so an infinite loop or crash
            # can't take the whole server down. -I = isolated mode (skip
            # user site, no PYTHONPATH inheritance) for hygiene.
            proc = await asyncio.create_subprocess_exec(
                # Use the running interpreter — there is no `python3.exe` on
                # Windows, which made the agent's `python` tool fail there.
                (sys.executable or "python"), "-I", "-c", content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
                cwd=workspace or _AGENT_WORKDIR,
            )
            stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
                proc,
                timeout=DEFAULT_PYTHON_TIMEOUT,
                progress_cb=progress_cb,
            )
            if timed_out:
                return {"error": f"python: timed out after {DEFAULT_PYTHON_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(stdout, MAX_OUTPUT_CHARS), "stderr": _truncate(stderr, MAX_OUTPUT_CHARS)}
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
            output = _truncate(output, MAX_OUTPUT_CHARS)
            return {"output": output or "(no output)", "exit_code": rc or 0}

        if tool == "read_file":
            # Args: plain path on line 1 (back-compat) OR JSON
            # {path, offset?, limit?} where offset/limit are a 1-based line range.
            raw_path, offset, limit = content.split("\n", 1)[0].strip(), 0, 0
            _stripped = content.strip()
            if _stripped.startswith("{"):
                try:
                    _a = json.loads(_stripped)
                    raw_path = str(_a.get("path", "")).strip()
                    offset = int(_a.get("offset") or 0)
                    limit = int(_a.get("limit") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            try:
                path = (_resolve_tool_path_in_workspace(workspace, raw_path)
                        if workspace else _resolve_tool_path(raw_path))
            except ValueError as e:
                return {"error": f"read_file: {e}", "exit_code": 1}
            try:
                # Run blocking read in a thread to keep the loop responsive.
                def _read():
                    if offset > 0 or limit > 0:
                        # Line-range read: slice [offset, offset+limit).
                        start = max(offset, 1)
                        out, n, budget = [], 0, MAX_READ_CHARS
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            for i, line in enumerate(f, 1):
                                if i < start:
                                    continue
                                if limit > 0 and n >= limit:
                                    break
                                out.append(line)
                                n += 1
                                budget -= len(line)
                                if budget <= 0:
                                    out.append(f"\n... [truncated at {MAX_READ_CHARS} chars]")
                                    break
                        return "".join(out)
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        return f.read(MAX_READ_CHARS + 1)
                data = await asyncio.to_thread(_read)
            except FileNotFoundError:
                return {"error": f"read_file: {path}: not found", "exit_code": 1}
            except PermissionError:
                return {"error": f"read_file: {path}: permission denied", "exit_code": 1}
            except IsADirectoryError:
                return {"error": f"read_file: {path}: is a directory (use ls)", "exit_code": 1}
            except OSError as e:
                return {"error": f"read_file: {path}: {e}", "exit_code": 1}
            if not (offset > 0 or limit > 0) and len(data) > MAX_READ_CHARS:
                data = data[:MAX_READ_CHARS] + f"\n... [truncated at {MAX_READ_CHARS} chars]"
            return {"output": data, "exit_code": 0}

        if tool == "write_file":
            lines = content.split("\n", 1)
            raw_path = lines[0].strip()
            body = lines[1] if len(lines) > 1 else ""
            try:
                path = (_resolve_tool_path_in_workspace(workspace, raw_path)
                        if workspace else _resolve_tool_path(raw_path))
            except ValueError as e:
                return {"error": f"write_file: {e}", "exit_code": 1}
            try:
                def _write():
                    # Capture prior content (best-effort, text) so we can show a
                    # before/after diff. Missing/binary file → treat as empty.
                    old = ""
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            old = f.read()
                    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError):
                        old = ""
                    d = os.path.dirname(path)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                    return old, len(body)
                old_content, size = await asyncio.to_thread(_write)
            except PermissionError:
                return {"error": f"write_file: {path}: permission denied", "exit_code": 1}
            except OSError as e:
                return {"error": f"write_file: {path}: {e}", "exit_code": 1}
            diff = _unified_diff(old_content, body, path)
            result = {"output": f"Wrote {size} bytes to {path}", "exit_code": 0}
            if diff:
                result["diff"] = diff
            return result

        if tool == "grep":
            # Args (JSON): {pattern, path?, glob?, ignore_case?, max_results?}.
            # Bare string → treated as the pattern.
            args: Dict[str, Any] = {}
            _s = (content or "").strip()
            if _s.startswith("{"):
                try:
                    args = json.loads(_s)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = {"pattern": _s}
            pattern = str(args.get("pattern", "")).strip()
            if not pattern:
                return {"error": "grep: pattern is required", "exit_code": 1}
            ignore_case = bool(args.get("ignore_case"))
            glob_pat = str(args.get("glob", "") or "").strip()
            try:
                max_hits = int(args.get("max_results") or _CODENAV_MAX_HITS)
            except (TypeError, ValueError):
                max_hits = _CODENAV_MAX_HITS
            max_hits = max(1, min(max_hits, _CODENAV_MAX_HITS))
            try:
                root = _resolve_search_root(str(args.get("path", "")), workspace)
            except ValueError as e:
                return {"error": f"grep: {e}", "exit_code": 1}

            def _grep():
                import re as _re
                import shutil
                rg = shutil.which("rg")
                if rg:
                    cmd = [rg, "--line-number", "--no-heading", "--color=never",
                           "--max-count", str(max_hits)]
                    if ignore_case:
                        cmd.append("--ignore-case")
                    if glob_pat:
                        cmd += ["--glob", glob_pat]
                    # Exclude junk dirs even when the tree has no .gitignore, so
                    # results match the Python fallback's skip set.
                    for _d in _CODENAV_SKIP_DIRS:
                        cmd += ["--glob", f"!**/{_d}/**"]
                    cmd += ["--regexp", pattern, root]
                    try:
                        import subprocess
                        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                        lines = [ln for ln in (p.stdout or "").splitlines() if ln][:max_hits]
                        return lines, None
                    except subprocess.TimeoutExpired:
                        return None, "grep: timed out"
                    except Exception as _e:
                        return None, f"grep: {_e}"
                # Python fallback (no ripgrep): walk + regex.
                try:
                    rx = _re.compile(pattern, _re.IGNORECASE if ignore_case else 0)
                except _re.error as _e:
                    return None, f"grep: bad pattern: {_e}"
                import fnmatch
                hits = []
                if os.path.isfile(root):
                    file_iter = [root]
                else:
                    file_iter = []
                    for dp, dns, fns in os.walk(root):
                        dns[:] = [d for d in dns if d not in _CODENAV_SKIP_DIRS]
                        for fn in fns:
                            if glob_pat and not fnmatch.fnmatch(fn, glob_pat):
                                continue
                            file_iter.append(os.path.join(dp, fn))
                for fp in file_iter:
                    if len(hits) >= max_hits:
                        break
                    try:
                        with open(fp, "r", encoding="utf-8", errors="strict") as f:
                            for i, line in enumerate(f, 1):
                                if rx.search(line):
                                    hits.append(f"{fp}:{i}:{line.rstrip()[:_CODENAV_MAX_LINE]}")
                                    if len(hits) >= max_hits:
                                        break
                    except (UnicodeDecodeError, OSError):
                        continue  # skip binary / unreadable
                return hits, None

            lines, err = await asyncio.to_thread(_grep)
            if err:
                return {"error": err, "exit_code": 1}
            if not lines:
                return {"output": f"No matches for {pattern!r} under {root}", "exit_code": 0}
            out = "\n".join(ln[:_CODENAV_MAX_LINE] for ln in lines)
            if len(lines) >= max_hits:
                out += f"\n... [capped at {max_hits} matches]"
            return {"output": _truncate(out), "exit_code": 0}

        if tool == "glob":
            args = {}
            _s = (content or "").strip()
            if _s.startswith("{"):
                try:
                    args = json.loads(_s)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = {"pattern": _s}
            pattern = str(args.get("pattern", "")).strip()
            if not pattern:
                return {"error": "glob: pattern is required", "exit_code": 1}
            try:
                root = _resolve_search_root(str(args.get("path", "")), workspace)
            except ValueError as e:
                return {"error": f"glob: {e}", "exit_code": 1}

            def _glob():
                from pathlib import Path
                base = Path(root)
                if not base.is_dir():
                    return None, f"glob: {root}: not a directory"
                matched = []
                try:
                    for p in base.rglob(pattern):
                        if set(p.relative_to(base).parts) & _CODENAV_SKIP_DIRS:
                            continue
                        try:
                            mtime = p.stat().st_mtime
                        except OSError:
                            mtime = 0
                        matched.append((mtime, str(p)))
                        if len(matched) > _CODENAV_MAX_HITS * 5:
                            break
                except (OSError, ValueError) as _e:
                    return None, f"glob: {_e}"
                matched.sort(key=lambda t: t[0], reverse=True)  # newest first
                return [pth for _, pth in matched[:_CODENAV_MAX_HITS]], None

            paths, err = await asyncio.to_thread(_glob)
            if err:
                return {"error": err, "exit_code": 1}
            if not paths:
                return {"output": f"No files matching {pattern!r} under {root}", "exit_code": 0}
            out = "\n".join(paths)
            if len(paths) >= _CODENAV_MAX_HITS:
                out += f"\n... [capped at {_CODENAV_MAX_HITS} files]"
            return {"output": _truncate(out), "exit_code": 0}

        if tool == "ls":
            raw_path = ""
            _s = (content or "").strip()
            if _s.startswith("{"):
                try:
                    raw_path = str(json.loads(_s).get("path", "")).strip()
                except json.JSONDecodeError:
                    raw_path = ""
            else:
                raw_path = _s.split("\n", 1)[0].strip()
            try:
                root = _resolve_search_root(raw_path, workspace)
            except ValueError as e:
                return {"error": f"ls: {e}", "exit_code": 1}

            def _ls():
                if not os.path.isdir(root):
                    return None, f"ls: {root}: not a directory"
                rows = []
                try:
                    with os.scandir(root) as it:
                        for entry in it:
                            if entry.name.startswith("."):
                                continue
                            try:
                                is_dir = entry.is_dir(follow_symlinks=False)
                                size = entry.stat(follow_symlinks=False).st_size if not is_dir else 0
                            except OSError:
                                continue
                            rows.append((is_dir, entry.name, size))
                except (PermissionError, OSError) as _e:
                    return None, f"ls: {_e}"
                rows.sort(key=lambda r: (not r[0], r[1].lower()))  # dirs first, then name
                lines = [f"{root}:"]
                for is_dir, name, size in rows[:_CODENAV_MAX_HITS]:
                    lines.append(f"  {name}/" if is_dir else f"  {name}  ({size} B)")
                if len(rows) > _CODENAV_MAX_HITS:
                    lines.append(f"  ... [{len(rows) - _CODENAV_MAX_HITS} more]")
                if not rows:
                    lines.append("  (empty)")
                return "\n".join(lines), None

            out, err = await asyncio.to_thread(_ls)
            if err:
                return {"error": err, "exit_code": 1}
            return {"output": _truncate(out), "exit_code": 0}

        if tool == "web_search":
            from src.search import comprehensive_web_search
            raw = content.strip()
            query = raw
            time_filter = None
            max_pages = 5
            # Allow JSON-shaped args: {"query": "...", "time_filter": "day", "max_pages": 7}
            if raw.startswith("{"):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and "query" in parsed:
                        query = str(parsed.get("query", "")).strip()
                        tf = parsed.get("time_filter") or parsed.get("freshness")
                        if isinstance(tf, str) and tf.lower() in ("day", "week", "month", "year"):
                            time_filter = tf.lower()
                        mp = parsed.get("max_pages")
                        if isinstance(mp, int) and 1 <= mp <= 10:
                            max_pages = mp
                except json.JSONDecodeError:
                    pass
            if not query:
                query = raw.split("\n")[0].strip()
            # Auto-detect freshness from query phrasing when not explicit
            if time_filter is None:
                q_lc = query.lower()
                if any(kw in q_lc for kw in ("today", "latest", "breaking", "this morning", "right now", "currently")):
                    time_filter = "day"
                elif any(kw in q_lc for kw in ("this week", "past week", "recent news", "last few days")):
                    time_filter = "week"
                elif any(kw in q_lc for kw in ("this month", "past month")):
                    time_filter = "month"
                elif " news" in q_lc or q_lc.startswith("news ") or q_lc.endswith(" news"):
                    time_filter = "week"
            loop = asyncio.get_running_loop()
            text, sources = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: comprehensive_web_search(
                        query,
                        max_pages=max_pages,
                        time_filter=time_filter,
                        return_sources=True,
                    ),
                ),
                timeout=30,
            )
            output = text[:MAX_OUTPUT_CHARS] if len(text) > MAX_OUTPUT_CHARS else text
            if sources:
                output += "\n\n<!-- SOURCES:" + json.dumps(sources) + " -->"
            return {"output": output, "exit_code": 0}

        if tool == "web_fetch":
            # Lightweight single-URL fetch. Wraps the SSRF-safe fetcher used
            # by deep research, so private/loopback/metadata addresses are
            # already blocked there.
            from src.search.content import fetch_webpage_content
            raw = content.strip()
            url = ""
            # Accept either a JSON arg ({"url": "..."}) or a plain URL/domain.
            if raw.startswith("{"):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        url = str(parsed.get("url") or "").strip()
                except json.JSONDecodeError:
                    url = ""
            if not url:
                # Non-JSON (or JSON without a usable url): take the first line
                # only, so a URL followed by commentary still parses.
                url = raw.split("\n")[0].strip()
            # Reject anything that isn't a single bare URL/domain token.
            if not url or url.startswith("{") or any(c in url for c in (" ", "\t", "\n")):
                return {"error": "web_fetch: provide a single URL or domain, e.g. example.com", "exit_code": 1}
            low = url.lower()
            if "://" in low and not low.startswith(("http://", "https://")):
                return {"error": f"web_fetch: unsupported URL scheme (only http/https): {url[:80]}", "exit_code": 1}
            # Accept bare domains like "example.com" by defaulting to https.
            if not low.startswith(("http://", "https://")):
                url = "https://" + url
            loop = asyncio.get_running_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: fetch_webpage_content(url, timeout=10)),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                return {"error": f"web_fetch: timed out fetching {url}", "exit_code": 1}
            except Exception as e:
                # Direct URL fetches can hit bot protection / auth walls
                # (e.g. eBay 403). Treat that as a tool failure the model can
                # reason around, not an uncaught chat-stream 500.
                return {"error": f"web_fetch: {url}: {e}", "exit_code": 1}
            err = result.get("error")
            text = (result.get("content") or "").strip()
            title = result.get("title") or ""

            if not text:
                if err:
                    return {"error": f"web_fetch: {url}: {err}", "exit_code": 1}
                # No extractable text: non-HTML body, or a pure client-rendered
                # shell. The agent can fall back to the builtin_browser tool.
                return {"error": f"web_fetch: {url}: no readable text content (not HTML, or the page needs JS/login)", "exit_code": 1}

            header = (f"# {title}\n" if title else "") + f"Source: {url}\n\n"
            output = header + text
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n\n[...truncated]"
            return {"output": output, "exit_code": 0}

        # manage_memory / generate_image still live as MCP servers
        # (mcp_servers/{memory,image_gen}_server.py); the MCP path above
        # handles them.
    except Exception as e:
        return {"error": f"{tool}: {e}", "exit_code": 1}

    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def execute_tool_block(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    tool_policy: Optional[ToolPolicy] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    workspace: Optional[str] = None,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    `progress_cb` is forwarded to long-running subprocess tools
    (bash, python) so the agent loop can emit `tool_progress` SSE
    events while the command is in flight. Ignored by other tools.
    """
    from src.tool_implementations import (
        do_create_document, do_update_document, do_edit_document,
        do_suggest_document, do_search_chats, do_manage_tasks,
        do_manage_skills, do_api_call, do_manage_endpoints,
        do_manage_mcp, do_manage_webhooks, do_manage_tokens,
        do_manage_documents, do_manage_settings, do_manage_notes,
        do_manage_calendar,
        do_download_model, do_serve_model, do_list_served_models, do_stop_served_model,
        do_tail_serve_output,
        do_list_downloads, do_cancel_download, do_search_hf_models, do_list_cached_models,
        do_list_serve_presets, do_serve_preset, do_adopt_served_model,
        do_list_cookbook_servers,
        do_edit_image, do_trigger_research, do_manage_research, do_resolve_contact,
        do_manage_contact,
        do_vault_search, do_vault_get, do_vault_unlock,
        do_app_api,
    )

    tool = block.tool_type
    content = block.content

    # Misformatted tool call detection: model put JSON inside ```python``` (or
    # similar) without naming the tool. Common with MiniMax-style outputs.
    # Return a helpful error so the model retries with the correct format.
    if tool in ("python", "json", "xml") and content.strip().startswith("{") and content.strip().endswith("}"):
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                desc = f"{tool}: misformatted tool call"
                result = {
                    "error": (
                        f"You wrote a JSON object inside a ```{tool}``` block, but that's not a tool call.\n"
                        "To call a tool, use the tool name as the fence tag, e.g.\n"
                        "```resolve_contact\n"
                        "{\"name\": \"...\"}\n"
                        "```\n"
                        "or\n"
                        "```send_email\n"
                        "{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}\n"
                        "```"
                    ),
                    "exit_code": 1,
                }
                return desc, result
        except (ValueError, TypeError):
            pass

    # Reject tools that the user has disabled for this request
    if tool_policy and tool_policy.blocks(tool):
        desc = f"{tool}: BLOCKED"
        result = {"error": tool_policy.reason_for(tool), "exit_code": 1}
        logger.info("Tool blocked by policy: %s", tool)
        return desc, result

    if disabled_tools and tool in disabled_tools:
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' is disabled by user.", "exit_code": 1}
        logger.info(f"Tool blocked by user: {tool}")
        return desc, result

    if tool in _ADMIN_TOOLS and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' requires an admin user.", "exit_code": 1}
        logger.warning("Admin tool blocked for non-admin owner=%r tool=%s", owner, tool)
        return desc, result

    if is_public_blocked_tool(tool) and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": (
                f"Tool '{tool}' is restricted to admin users on this deployment. "
                "Ask an admin to perform this action or grant the needed permission."
            ),
            "exit_code": 1,
        }
        logger.warning("Public tool policy blocked owner=%r tool=%s", owner, tool)
        return desc, result

    # ask_user: the agent poses a multiple-choice question to the user to get a
    # decision/clarification. This is a pure UI-control marker — no subprocess,
    # no filesystem. It returns an `ask_user` payload that the agent loop turns
    # into an `ask_user` SSE event and then ENDS the turn, so the chat waits for
    # the user's selection (their choice arrives as the next message).
    if tool == "ask_user":
        question, options, multi = "", [], False
        raw = (content or "").strip()
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}
        if isinstance(parsed, dict):
            question = str(parsed.get("question", "")).strip()
            multi = bool(parsed.get("multi") or parsed.get("multiSelect"))
            for opt in (parsed.get("options") or []):
                if isinstance(opt, dict):
                    label = str(opt.get("label", "")).strip()
                    descr = str(opt.get("description", "")).strip()
                elif isinstance(opt, str):
                    label, descr = opt.strip(), ""
                else:
                    continue
                if label:
                    options.append({"label": label, "description": descr})
        else:
            question = raw
        if not question or len(options) < 2:
            return "ask_user: invalid", {
                "error": (
                    "ask_user needs a non-empty `question` and at least 2 `options` "
                    "(each an object with a `label`, optional `description`)."
                ),
                "exit_code": 1,
            }
        options = options[:6]  # keep the choice list sane
        desc = f"ask_user: {question[:80]}"
        labels = ", ".join(o["label"] for o in options)
        result = {
            "ask_user": {"question": question, "options": options, "multi": multi},
            "output": f"Asked the user: {question}\nOptions: {labels}\nAwaiting their selection.",
            "exit_code": 0,
        }
        logger.info("Tool executed: %s (%d options, multi=%s)", desc, len(options), multi)
        return desc, result

    # update_plan: the agent writes back to the active plan — tick an item done
    # or revise steps (e.g. when the user asks to change something). Pure UI
    # marker: returns a `plan_update` payload the agent loop turns into a
    # `plan_update` SSE event; the frontend replaces the stored plan and refreshes
    # the docked plan window. Does NOT end the turn.
    if tool == "update_plan":
        import json as _json
        raw = (content or "").strip()
        plan = ""
        try:
            parsed = _json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}
        if isinstance(parsed, dict) and parsed.get("plan"):
            plan = str(parsed.get("plan", "")).strip()
        else:
            # Plain-string call (raw checklist) or JSON without a usable `plan`.
            plan = raw
        if not plan:
            return "update_plan: invalid", {
                "error": "update_plan needs a non-empty `plan` (the full updated checklist as markdown).",
                "exit_code": 1,
            }
        plan = plan[:8192]
        done = plan.count("- [x]") + plan.count("- [X]")
        total = done + plan.count("- [ ]")
        desc = f"update_plan: {done}/{total} done" if total else "update_plan"
        result = {
            "plan_update": {"plan": plan},
            "output": f"Plan updated ({done}/{total} steps complete)." if total else "Plan updated.",
            "exit_code": 0,
        }
        logger.info("Tool executed: %s", desc)
        return desc, result

    # Background execution: a `bash` block whose first line is the `#!bg`
    # marker runs DETACHED — returns a job id immediately so the chat stream
    # isn't held open for a multi-minute install/ffmpeg/download. The always-on
    # monitor re-invokes the agent with the full output when the job finishes.
    if tool == "bash" and session_id:
        _is_bg, _bg_cmd = _split_bg_marker(content)
        if _is_bg and _bg_cmd:
            from src import bg_jobs
            rec = bg_jobs.launch(_bg_cmd, session_id=session_id, cwd=workspace or _AGENT_WORKDIR)
            short = _bg_cmd.strip().split(chr(10))[0][:80]
            desc = f"bash (background): {short}"
            result = {
                "output": (
                    f"Started background job `{rec['id']}`. It is running detached — "
                    f"do NOT wait for it or poll it. You will be automatically re-invoked "
                    f"with its full output when it finishes. Continue with other work, or "
                    f"end your turn now and resume when the result arrives."
                ),
                "exit_code": 0,
                "bg_job_id": rec["id"],
            }
            logger.info(f"Tool executed: {desc} -> bg job {rec['id']}")
            return desc, result

    # Route MCP-extracted tools through the MCP manager. Forward
    # the progress callback so long-running subprocess tools
    # (bash, python) can stream `tool_progress` events to the UI.
    if tool in _MCP_TOOL_MAP:
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _call_mcp_tool(tool, content, progress_cb=progress_cb, workspace=workspace)
    elif tool in ("grep", "glob", "ls"):
        # Code-navigation tools — no MCP server; run the direct implementation.
        # Confined to the workspace when one is set (same policy as read_file).
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _direct_fallback(tool, content, progress_cb=progress_cb, workspace=workspace) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "create_document":
        title = content.split("\n")[0].strip()[:60]
        desc = f"create_document: {title}"
        result = await do_create_document(content, session_id=session_id, owner=owner)
    elif tool == "update_document":
        desc = f"update_document: {content.split(chr(10))[0][:60]}"
        result = await do_update_document(content, owner=owner)
    elif tool == "edit_document":
        result = await do_edit_document(content, owner=owner)
        desc = f"edit_document: {result.get('title', '')}"
    elif tool == "suggest_document":
        result = await do_suggest_document(content, owner=owner)
        desc = f"suggest_document: {result.get('count', 0)} suggestions"
    elif tool == "search_chats":
        query = content.split("\n")[0].strip()
        desc = f"search_chats: {query[:80]}"
        result = await do_search_chats(query, owner=owner)
    elif tool in ("chat_with_model", "create_session", "list_sessions",
                  "send_to_session", "pipeline",
                  "manage_session", "manage_memory", "list_models",
                  "ui_control", "ask_teacher"):
        from src.ai_interaction import dispatch_ai_tool
        desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    elif tool == "manage_tasks":
        desc = "manage_tasks"
        result = await do_manage_tasks(content, owner=owner)
    elif tool == "manage_skills":
        desc = "manage_skills"
        result = await do_manage_skills(content, owner=owner)
    elif tool == "api_call":
        first_line = content.split("\n")[0].strip()[:60]
        desc = f"api_call: {first_line}"
        result = await do_api_call(content)
    elif tool == "manage_endpoints":
        desc = "manage_endpoints"
        result = await do_manage_endpoints(content, owner=owner)
    elif tool == "manage_mcp":
        desc = "manage_mcp"
        result = await do_manage_mcp(content, owner=owner)
    elif tool == "manage_webhooks":
        desc = "manage_webhooks"
        result = await do_manage_webhooks(content, owner=owner)
    elif tool == "manage_tokens":
        desc = "manage_tokens"
        result = await do_manage_tokens(content, owner=owner)
    elif tool == "manage_documents":
        desc = "manage_documents"
        result = await do_manage_documents(content, owner=owner)
    elif tool == "manage_settings":
        desc = "manage_settings"
        result = await do_manage_settings(content, owner=owner)
    elif tool == "manage_notes":
        desc = "manage_notes"
        result = await do_manage_notes(content, owner=owner)
    elif tool == "manage_calendar":
        desc = "manage_calendar"
        result = await do_manage_calendar(content, owner=owner)
    elif tool == "download_model":
        desc = "download_model"
        result = await do_download_model(content, owner=owner)
    elif tool == "serve_model":
        desc = "serve_model"
        result = await do_serve_model(content, owner=owner)
    elif tool == "list_served_models":
        desc = "list_served_models"
        result = await do_list_served_models(content, owner=owner)
    elif tool == "stop_served_model":
        desc = "stop_served_model"
        result = await do_stop_served_model(content, owner=owner)
    elif tool == "tail_serve_output":
        desc = "tail_serve_output"
        result = await do_tail_serve_output(content, owner=owner)
    elif tool == "list_downloads":
        desc = "list_downloads"
        result = await do_list_downloads(content, owner=owner)
    elif tool == "cancel_download":
        desc = "cancel_download"
        result = await do_cancel_download(content, owner=owner)
    elif tool == "search_hf_models":
        desc = "search_hf_models"
        result = await do_search_hf_models(content, owner=owner)
    elif tool == "list_cached_models":
        desc = "list_cached_models"
        result = await do_list_cached_models(content, owner=owner)
    elif tool == "app_api":
        desc = "app_api"
        result = await do_app_api(content, owner=owner)
    elif tool == "list_serve_presets":
        desc = "list_serve_presets"
        result = await do_list_serve_presets(content, owner=owner)
    elif tool == "serve_preset":
        desc = "serve_preset"
        result = await do_serve_preset(content, owner=owner)
    elif tool == "adopt_served_model":
        desc = "adopt_served_model"
        result = await do_adopt_served_model(content, owner=owner)
    elif tool == "list_cookbook_servers":
        desc = "list_cookbook_servers"
        result = await do_list_cookbook_servers(content, owner=owner)
    elif tool == "edit_image":
        desc = "edit_image"
        result = await do_edit_image(content, owner=owner)
    elif tool == "edit_file":
        result = await _do_edit_file(content, workspace=workspace)
        desc = result.get("output") or result.get("error") or "edit_file"
    elif tool == "trigger_research":
        desc = "trigger_research"
        result = await do_trigger_research(content, owner=owner)
    elif tool == "manage_research":
        desc = "manage_research"
        result = await do_manage_research(content, owner=owner)
    elif tool == "resolve_contact":
        desc = "resolve_contact"
        result = await do_resolve_contact(content, owner=owner)
    elif tool == "manage_contact":
        desc = "manage_contact"
        result = await do_manage_contact(content, owner=owner)
    elif tool == "vault_search":
        desc = "vault_search"
        result = await do_vault_search(content, owner=owner)
    elif tool == "vault_get":
        desc = "vault_get"
        result = await do_vault_get(content, owner=owner)
    elif tool == "vault_unlock":
        desc = "vault_unlock"
        result = await do_vault_unlock(content, owner=owner)
    elif tool.startswith("mcp__"):
        # MCP tool dispatch
        mcp = get_mcp_manager()
        if mcp:
            try:
                args = json.loads(content) if content.strip().startswith("{") else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            desc = f"mcp: {tool}"
            result = await mcp.call_tool(tool, args)
        else:
            desc = f"mcp: {tool}"
            result = {"error": "MCP manager not available", "exit_code": 1}
    else:
        desc = f"unknown: {tool}"
        result = {"error": f"Unknown tool type: {tool}", "exit_code": 1}

    logger.info(f"Tool executed: {desc} -> exit_code={result.get('exit_code', 'n/a')}")
    return desc, result


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

# Keys handled by the dedicated branches below — never echo them as raw JSON.
_FORMATTER_HANDLED_KEYS = {
    "stdout", "stderr", "exit_code", "content", "size",
    "response", "results", "session_id", "name", "model", "session_name",
    "success", "path", "action", "title", "doc_id", "version", "applied",
    "error", "output",
}


def format_tool_result(description: str, result: Dict) -> str:
    """Format a tool result into text for feeding back to the LLM."""
    parts = [f"### {description}"]

    if "stdout" in result:
        if result["stdout"]:
            parts.append(f"**stdout:**\n```\n{result['stdout']}\n```")
        if result["stderr"]:
            parts.append(f"**stderr:**\n```\n{result['stderr']}\n```")
        parts.append(f"**exit_code:** {result.get('exit_code', 'unknown')}")
    elif "output" in result:
        # bash / python canonical result shape: {"output": ..., "exit_code": ...}
        parts.append(f"```\n{result['output']}\n```")
        if result.get("exit_code") not in (0, None):
            parts.append(f"**exit_code:** {result['exit_code']}")
    elif "content" in result:
        parts.append(f"**content ({result.get('size', '?')} chars):**\n```\n{result['content']}\n```")
    elif "response" in result:
        model = result.get("model", result.get("session_name", ""))
        if model:
            parts.append(f"**{model} responded:**\n{result['response']}")
        else:
            parts.append(result["response"])
    elif "results" in result:
        parts.append(result["results"])
    elif "session_id" in result and "name" in result:
        parts.append(f"Session created: **{result['name']}** (id: `{result['session_id']}`, model: {result.get('model', 'unknown')})")
    elif "success" in result:
        if result["success"]:
            parts.append(f"File written: {result['path']} ({result['size']} bytes)")
        else:
            parts.append(f"Error: {result.get('error', 'unknown')}")
    elif "action" in result:
        action = result["action"]
        if action == "create":
            parts.append(f"Document created: \"{result.get('title', '')}\" (id: {result['doc_id']}, v{result['version']})")
        elif action == "update":
            parts.append(f"Document updated: \"{result.get('title', '')}\" (v{result['version']})")
        elif action == "edit":
            parts.append(f'Document edited: "{result.get("title", "")}" (v{result.get("version", "?")}, {result.get("applied", 0)} edit(s) applied)')
    elif "error" in result:
        parts.append(f"**Error:** {result['error']}")

    # Surface any additional structured payload (events, tasks, notes, calendars,
    # documents, attachments, etc.) that the dedicated branches above don't show.
    # Without this, tools that return {"response": "...", "events": [...]} would
    # silently drop the events list and the model would only see the summary line.
    extra = {k: v for k, v in result.items() if k not in _FORMATTER_HANDLED_KEYS}
    if extra:
        try:
            extra_json = json.dumps(extra, indent=2, default=str, ensure_ascii=False)
            # Cap to avoid blowing the context window on huge payloads.
            if len(extra_json) > 8000:
                extra_json = extra_json[:8000] + f"\n... (truncated, {len(extra_json)} chars total)"
            parts.append(f"**data:**\n```json\n{extra_json}\n```")
        except (TypeError, ValueError):
            pass

    return "\n".join(parts)
