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
import re
import sys
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.tool_security import is_public_blocked_tool, owner_is_admin_or_single_user

MAX_OUTPUT_CHARS = 10_000

# Wall-clock + file-count bounds for the pure-Python grep/glob fallback (used
# only when ripgrep is unavailable). The rg path is already bounded by its own
# subprocess timeout; these guarantee the fallback can never hang on a huge tree.
FALLBACK_SCAN_TIMEOUT_S = 30
FALLBACK_SCAN_MAX_FILES = 20_000
MAX_READ_CHARS = 20_000

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
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_settings",
    "download_model",
    "serve_model",
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
    "edit_file":      ("filesystem", "edit_file"),
    "glob":           ("filesystem", "glob"),
    "grep":           ("filesystem", "grep"),
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


# Fenced markers for the edit_file conflict-style block.
_EDIT_OLD_MARKER = "<<<<<<< OLD"
_EDIT_SEP_MARKER = "======="
_EDIT_NEW_MARKER = ">>>>>>> NEW"


def _parse_edit_file(content: str) -> Dict:
    """Parse the conflict-style edit_file block into structured args.

    Format (path line first, then an OLD/=======/NEW conflict block):
        <path> [replace_all]
        <<<<<<< OLD
        old text (verbatim)
        =======
        new text
        >>>>>>> NEW

    `replace_all` on the path line forces every-occurrence replacement.
    An empty OLD block means create-new-file mode.
    """
    raw_lines = content.split("\n")
    if not raw_lines:
        return {"path": "", "old_string": "", "new_string": ""}
    path_line = raw_lines[0].strip()
    replace_all = False
    # Trailing `replace_all` / `replace-all` marker on the path line.
    m = re.match(r"^(.*?)[ \t]+(replace[_-]?all)\s*$", path_line, re.IGNORECASE)
    if m:
        path_line = m.group(1).strip()
        replace_all = True
    path = path_line.strip().strip('"').strip("'")

    body = raw_lines[1:]
    # Locate the conflict markers.
    old_idx = sep_idx = new_idx = None
    for i, ln in enumerate(body):
        s = ln.strip()
        if old_idx is None and s == _EDIT_OLD_MARKER:
            old_idx = i
        elif old_idx is not None and sep_idx is None and s == _EDIT_SEP_MARKER:
            sep_idx = i
        elif sep_idx is not None and new_idx is None and s == _EDIT_NEW_MARKER:
            new_idx = i
            break

    if old_idx is not None and sep_idx is not None and new_idx is not None:
        old_string = "\n".join(body[old_idx + 1:sep_idx])
        new_string = "\n".join(body[sep_idx + 1:new_idx])
    else:
        # Markers missing/malformed — treat the whole remainder as new
        # content for create-mode so the model still gets a useful error
        # from the executor rather than a silent no-op.
        old_string = ""
        new_string = "\n".join(body)
    return {
        "path": path,
        "old_string": old_string,
        "new_string": new_string,
        "replace_all": replace_all,
    }


def _parse_glob(content: str) -> Dict:
    lines = content.strip().split("\n")
    args = {"pattern": lines[0].strip() if lines else ""}
    if len(lines) > 1 and lines[1].strip():
        args["path"] = lines[1].strip()
    return args


def _parse_grep(content: str) -> Dict:
    """Parse grep args. Accepts a JSON object (preferred, from the native
    converter) or a plain pattern on the first line with an optional
    `flags` line of CLI-style tokens (`-i`, `-n`, `glob=*.py`, etc.)."""
    raw = content.strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("pattern"):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    lines = raw.split("\n")
    args: Dict[str, Any] = {"pattern": lines[0].strip() if lines else ""}
    if len(lines) > 1 and lines[1].strip():
        # Second line: whitespace-separated flag tokens.
        for tok in lines[1].split():
            if tok in ("-i", "--ignore-case"):
                args["-i"] = True
            elif tok in ("-n", "--line-number"):
                args["-n"] = True
            elif tok in ("-l", "--files-with-matches"):
                args["output_mode"] = "files_with_matches"
            elif tok in ("-c", "--count"):
                args["output_mode"] = "count"
            elif tok.startswith("glob="):
                args["glob"] = tok[len("glob="):]
            elif tok.startswith("path="):
                args["path"] = tok[len("path="):]
            elif tok.startswith("mode="):
                args["output_mode"] = tok[len("mode="):]
    return args


_MCP_ARG_PARSERS: Dict[str, callable] = {
    "bash":           lambda c: {"command": c},
    "python":         lambda c: {"code": c},
    "web_search":     lambda c: {"query": c.split("\n")[0].strip()},
    "web_fetch":      lambda c: {"url": c.split("\n")[0].strip()},
    "read_file":      lambda c: {"path": c.split("\n")[0].strip()},
    "write_file":     _parse_write_file,
    "edit_file":      _parse_edit_file,
    "glob":           _parse_glob,
    "grep":           _parse_grep,
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
) -> Dict:
    """Route a legacy tool call through the MCP manager, with direct fallbacks."""
    mcp = get_mcp_manager()
    if not mcp:
        return await _direct_fallback(tool, content, progress_cb=progress_cb) or {"error": f"MCP manager not available for tool '{tool}'", "exit_code": 1}

    server_id, tool_name = _MCP_TOOL_MAP[tool]
    qualified = f"mcp__{server_id}__{tool_name}"
    args = _build_mcp_args(tool, content)
    result = await mcp.call_tool(qualified, args)

    # If MCP server not connected, try direct fallback
    if isinstance(result, dict) and result.get("exit_code") == 1 and "not connected" in result.get("error", ""):
        fallback = await _direct_fallback(tool, content, progress_cb=progress_cb)
        if fallback:
            return fallback

    return result


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


# ---------------------------------------------------------------------------
# Filesystem confinement for the agent FILE tools
# (read_file / write_file / edit_file / glob / grep)
# ---------------------------------------------------------------------------
# These tools are confined to a workspace root and resolve + validate every
# path on each call. Unlike bash/python — a real shell can always cd out, so
# starting it in a root is only a guardrail — the file tools never spawn a
# shell, so this IS an enforced boundary for them. Enforcement lives here in
# the executor, which every call path (local-model fenced blocks AND hosted
# tool_calls, both serialized to the same fenced content) funnels through, so
# the boundary cannot be bypassed by choosing a different call path.

def _agent_fs_root() -> str:
    """Absolute, symlink-resolved workspace root the file tools are confined to.

    Defaults to the process working directory (the project/install dir Odysseus
    runs from). Override with the ``ODYSSEUS_AGENT_FS_ROOT`` env var or the
    ``agent_fs_root`` app setting to point the agent at a different workspace.
    """
    root = os.environ.get("ODYSSEUS_AGENT_FS_ROOT")
    if not root:
        try:
            from src.settings import get_setting
            root = get_setting("agent_fs_root", "") or None
        except Exception:
            root = None
    if not root:
        root = os.getcwd()
    try:
        return os.path.realpath(root)
    except Exception:
        return os.path.abspath(root)


def _within_root(root: str, real_path: str) -> bool:
    """True if ``real_path`` is ``root`` itself or nested inside it. Case- and
    separator-normalized so it is correct on case-insensitive filesystems
    (Windows, macOS) and never false-positives on a sibling like
    ``/work-other`` when the root is ``/work``."""
    r = os.path.normcase(root.rstrip(os.sep)) or os.sep
    p = os.path.normcase(real_path)
    return p == r or p.startswith(r + os.sep)


def _resolve_in_root(path: str):
    """Resolve a file-tool path against the workspace root, rejecting escapes.

    Returns ``(resolved_abs_path, None)`` when the path is safe, or
    ``(None, error_message)`` when it must be refused. ``os.path.realpath``
    resolves symlinks on the existing portion of the path and lexically
    normalizes the rest (including ``..``), so every escape vector collapses to
    one containment check:

      * absolute paths outside the root  -> rejected
      * ``..`` traversal                 -> rejected after normalization
      * a symlink (leaf or parent dir) whose target leaves the root -> rejected
        (for not-yet-existing create paths the symlinked parent is still
        resolved, so it can't be used as an escape hatch)
    """
    if path is None or not str(path).strip():
        return None, "path required"
    root = _agent_fs_root()
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    try:
        real = os.path.realpath(candidate)
    except (OSError, ValueError):
        return None, f"invalid path: {path}"
    if _within_root(root, real):
        return real, None
    return None, f"path is outside the agent workspace root: {path}"


async def _direct_fallback(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Optional[Dict]:
    """In-process execution path for the eight tools that used to live as
    stdio MCP servers under mcp_servers/. Those servers were deleted in
    favor of native execution; this function is now the canonical path,
    not a fallback. The name is kept for backwards compat with callers.

    `progress_cb` is called periodically while bash/python subprocesses
    are still running, with `{elapsed_s, tail}` payloads. Other tools
    ignore it.
    """
    import json as _json

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
    }

    try:
        if tool == "bash":
            proc = await asyncio.create_subprocess_shell(
                content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
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
            path = content.split("\n", 1)[0].strip()
            if not path:
                return {"error": "read_file: path required", "exit_code": 1}
            path, _err = _resolve_in_root(path)
            if _err:
                return {"error": f"read_file: {_err}", "exit_code": 1}
            try:
                # Run blocking read in a thread to keep the loop responsive
                def _read():
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        return f.read(MAX_READ_CHARS + 1)
                data = await asyncio.to_thread(_read)
            except FileNotFoundError:
                return {"error": f"read_file: {path}: not found", "exit_code": 1}
            except PermissionError:
                return {"error": f"read_file: {path}: permission denied", "exit_code": 1}
            except OSError as e:
                return {"error": f"read_file: {path}: {e}", "exit_code": 1}
            truncated = len(data) > MAX_READ_CHARS
            if truncated:
                data = data[:MAX_READ_CHARS] + f"\n... [truncated at {MAX_READ_CHARS} chars]"
            return {"output": data, "exit_code": 0}

        if tool == "write_file":
            lines = content.split("\n", 1)
            path = lines[0].strip()
            body = lines[1] if len(lines) > 1 else ""
            if not path:
                return {"error": "write_file: path required", "exit_code": 1}
            path, _err = _resolve_in_root(path)
            if _err:
                return {"error": f"write_file: {_err}", "exit_code": 1}
            try:
                def _write():
                    import os
                    d = os.path.dirname(path)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                    return len(body)
                size = await asyncio.to_thread(_write)
            except PermissionError:
                return {"error": f"write_file: {path}: permission denied", "exit_code": 1}
            except OSError as e:
                return {"error": f"write_file: {path}: {e}", "exit_code": 1}
            return {"output": f"Wrote {size} bytes to {path}", "exit_code": 0}

        if tool == "edit_file":
            args = _parse_edit_file(content)
            path = (args.get("path") or "").strip()
            old_string = args.get("old_string", "")
            new_string = args.get("new_string", "")
            replace_all = bool(args.get("replace_all"))
            if not path:
                return {"error": "edit_file: path required", "exit_code": 1}
            path, _err = _resolve_in_root(path)
            if _err:
                return {"error": f"edit_file: {_err}", "exit_code": 1}

            def _edit():
                exists = os.path.exists(path)
                # Create-new-file mode: empty old_string.
                if old_string == "":
                    if exists:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            existing = f.read()
                        if existing.strip():
                            raise ValueError(
                                f"edit_file: {path} already exists and is non-empty — "
                                "use a non-empty old_string to edit it"
                            )
                    d = os.path.dirname(path)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_string)
                    return 1
                # Edit mode: file must exist.
                if not exists:
                    raise FileNotFoundError(path)
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    data = f.read()
                count = data.count(old_string)
                if count == 0:
                    raise ValueError(f"edit_file: old_string not found in {path}")
                if count > 1 and not replace_all:
                    raise ValueError(
                        f"edit_file: old_string is not unique ({count} matches) — "
                        "add more context or set replace_all"
                    )
                if new_string == old_string:
                    raise ValueError("edit_file: new_string equals old_string — no change")
                if replace_all:
                    updated = data.replace(old_string, new_string)
                    n = count
                else:
                    updated = data.replace(old_string, new_string, 1)
                    n = 1
                with open(path, "w", encoding="utf-8") as f:
                    f.write(updated)
                return n

            try:
                n = await asyncio.to_thread(_edit)
            except FileNotFoundError:
                return {"error": f"edit_file: {path}: not found", "exit_code": 1}
            except PermissionError:
                return {"error": f"edit_file: {path}: permission denied", "exit_code": 1}
            except ValueError as e:
                return {"error": str(e), "exit_code": 1}
            except OSError as e:
                return {"error": f"edit_file: {path}: {e}", "exit_code": 1}
            return {"output": f"Edited {path} ({n} replacement(s))", "exit_code": 0}

        if tool == "glob":
            import glob as _glob
            import shutil as _shutil
            args = _parse_glob(content)
            pattern = (args.get("pattern") or "").strip()
            if not pattern:
                return {"error": "glob: pattern required", "exit_code": 1}
            agent_root = _agent_fs_root()
            _raw_root = (args.get("path") or "").strip()
            if _raw_root:
                root, _err = _resolve_in_root(_raw_root)
                if _err:
                    return {"error": f"glob: {_err}", "exit_code": 1}
            else:
                root = agent_root

            def _do_glob():
                matches: list[str] = []
                # Prefer ripgrep for speed when available.
                rg = _shutil.which("rg")
                if rg:
                    import subprocess
                    try:
                        out = subprocess.run(
                            [rg, "--files", "--glob", pattern],
                            cwd=root, capture_output=True, text=True, timeout=30,
                        )
                        if out.returncode in (0, 1):
                            for line in out.stdout.splitlines():
                                line = line.strip()
                                if not line:
                                    continue
                                full = os.path.join(root, line)
                                matches.append(full)
                    except (OSError, subprocess.SubprocessError):
                        matches = []
                if not matches:
                    # Pure-Python fallback (no ripgrep). Lazy iglob bounded by a
                    # wall-clock deadline + match cap so a huge tree can't hang it.
                    pat = os.path.join(root, pattern)
                    _deadline = time.monotonic() + FALLBACK_SCAN_TIMEOUT_S
                    for p in _glob.iglob(pat, recursive=True):
                        if os.path.isfile(p):
                            matches.append(p)
                        if (len(matches) >= FALLBACK_SCAN_MAX_FILES
                                or time.monotonic() > _deadline):
                            break
                # Defense in depth: drop anything that resolves outside the
                # workspace root (e.g. a symlinked directory the glob followed).
                matches = [p for p in matches
                           if _within_root(agent_root, os.path.realpath(p))]
                # Sort newest-mtime first; missing files sort last.
                def _mtime(p):
                    try:
                        return os.stat(p).st_mtime
                    except OSError:
                        return 0.0
                matches.sort(key=_mtime, reverse=True)
                # De-dupe while preserving order.
                seen = set()
                uniq = []
                for p in matches:
                    rp = os.path.relpath(p, root)
                    if rp in seen:
                        continue
                    seen.add(rp)
                    uniq.append(rp)
                return uniq

            try:
                results = await asyncio.to_thread(_do_glob)
            except OSError as e:
                return {"error": f"glob: {e}", "exit_code": 1}
            if not results:
                return {"output": "No files found", "exit_code": 0}
            truncated = len(results) > 100
            shown = results[:100]
            body = "\n".join(shown)
            if truncated:
                body += f"\n... ({len(results)} matches, showing first 100)"
            return {"output": body, "exit_code": 0}

        if tool == "grep":
            import shutil as _shutil
            args = _parse_grep(content)
            pattern = args.get("pattern") or ""
            if not pattern:
                return {"error": "grep: pattern required", "exit_code": 1}
            _raw_sp = (args.get("path") or "").strip()
            if _raw_sp:
                search_path, _err = _resolve_in_root(_raw_sp)
                if _err:
                    return {"error": f"grep: {_err}", "exit_code": 1}
            else:
                search_path = _agent_fs_root()
            file_glob = args.get("glob")
            mode = args.get("output_mode") or "files_with_matches"
            ignore_case = bool(args.get("-i"))
            show_lines = args.get("-n")
            if show_lines is None:
                show_lines = True
            try:
                head_limit = int(args.get("head_limit") or 250)
            except (TypeError, ValueError):
                head_limit = 250
            GREP_MAX_CHARS = 20_000

            def _do_grep():
                rg = _shutil.which("rg")
                if rg:
                    import subprocess
                    cmd = [rg, "--no-heading", "--color", "never"]
                    if ignore_case:
                        cmd.append("-i")
                    if file_glob:
                        cmd += ["--glob", file_glob]
                    if mode == "files_with_matches":
                        cmd.append("-l")
                    elif mode == "count":
                        cmd.append("-c")
                    else:  # content
                        if show_lines:
                            cmd.append("-n")
                    cmd += [pattern, search_path]
                    try:
                        out = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=30,
                        )
                        if out.returncode in (0, 1):
                            lines = [l for l in out.stdout.splitlines() if l.strip()]
                            return lines
                    except (OSError, subprocess.SubprocessError):
                        pass
                # Pure-Python fallback.
                import fnmatch
                flags = re.IGNORECASE if ignore_case else 0
                try:
                    rx = re.compile(pattern, flags)
                except re.error as e:
                    raise ValueError(f"grep: invalid regex: {e}")
                results: list[str] = []
                files_with: list[str] = []
                counts: Dict[str, int] = {}
                _search_is_file = os.path.isfile(search_path)

                def _rel(p):
                    # When searching a single file, show its basename rather
                    # than relpath(file, file) which is just ".".
                    if _search_is_file:
                        return os.path.basename(p)
                    return os.path.relpath(p, search_path)

                def _iter_files():
                    if os.path.isfile(search_path):
                        yield search_path
                        return
                    for dirpath, dirnames, filenames in os.walk(search_path):
                        # Exclude .git.
                        dirnames[:] = [d for d in dirnames if d != ".git"]
                        for fn in filenames:
                            if file_glob and not fnmatch.fnmatch(fn, file_glob):
                                continue
                            yield os.path.join(dirpath, fn)

                _deadline = time.monotonic() + FALLBACK_SCAN_TIMEOUT_S
                _scanned = 0
                for fpath in _iter_files():
                    if (_scanned >= FALLBACK_SCAN_MAX_FILES
                            or time.monotonic() > _deadline):
                        break
                    _scanned += 1
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            matched_file = False
                            for lineno, line in enumerate(f, 1):
                                if rx.search(line):
                                    matched_file = True
                                    counts[fpath] = counts.get(fpath, 0) + 1
                                    if mode == "content":
                                        rel = _rel(fpath)
                                        text = line.rstrip("\n")
                                        if show_lines:
                                            results.append(f"{rel}:{lineno}:{text}")
                                        else:
                                            results.append(f"{rel}:{text}")
                            if matched_file:
                                files_with.append(_rel(fpath))
                    except (OSError, UnicodeError):
                        continue

                if mode == "files_with_matches":
                    return files_with
                if mode == "count":
                    return [f"{_rel(p)}:{c}" for p, c in counts.items()]
                return results

            try:
                lines = await asyncio.to_thread(_do_grep)
            except ValueError as e:
                return {"error": str(e), "exit_code": 1}
            except OSError as e:
                return {"error": f"grep: {e}", "exit_code": 1}
            if not lines:
                return {"output": "No matches found", "exit_code": 0}
            truncated = len(lines) > head_limit
            shown = lines[:head_limit]
            body = "\n".join(shown)
            if len(body) > GREP_MAX_CHARS:
                body = body[:GREP_MAX_CHARS] + "\n... (output truncated)"
            elif truncated:
                body += f"\n... ({len(lines)} matches, showing first {head_limit})"
            return {"output": body, "exit_code": 0}

        if tool == "web_search":
            from src.search import comprehensive_web_search
            raw = content.strip()
            query = raw
            time_filter = None
            max_pages = 5
            # Allow JSON-shaped args: {"query": "...", "time_filter": "day", "max_pages": 7}
            if raw.startswith("{"):
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict) and "query" in parsed:
                        query = str(parsed.get("query", "")).strip()
                        tf = parsed.get("time_filter") or parsed.get("freshness")
                        if isinstance(tf, str) and tf.lower() in ("day", "week", "month", "year"):
                            time_filter = tf.lower()
                        mp = parsed.get("max_pages")
                        if isinstance(mp, int) and 1 <= mp <= 10:
                            max_pages = mp
                except _json.JSONDecodeError:
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
                output += "\n\n<!-- SOURCES:" + _json.dumps(sources) + " -->"
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
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        url = str(parsed.get("url") or "").strip()
                except _json.JSONDecodeError:
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
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
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
            import json as _json
            parsed = _json.loads(content.strip())
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

    # Background execution: a `bash` block whose first line is the `#!bg`
    # marker runs DETACHED — returns a job id immediately so the chat stream
    # isn't held open for a multi-minute install/ffmpeg/download. The always-on
    # monitor re-invokes the agent with the full output when the job finishes.
    if tool == "bash" and session_id:
        _is_bg, _bg_cmd = _split_bg_marker(content)
        if _is_bg and _bg_cmd:
            from src import bg_jobs
            rec = bg_jobs.launch(_bg_cmd, session_id=session_id)
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
        result = await _call_mcp_tool(tool, content, progress_cb=progress_cb)
    elif tool == "create_document":
        title = content.split("\n")[0].strip()[:60]
        desc = f"create_document: {title}"
        result = await do_create_document(content, session_id=session_id)
    elif tool == "update_document":
        desc = f"update_document: {content.split(chr(10))[0][:60]}"
        result = await do_update_document(content)
    elif tool == "edit_document":
        result = await do_edit_document(content)
        desc = f"edit_document: {result.get('title', '')}"
    elif tool == "suggest_document":
        result = await do_suggest_document(content)
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
        result = {"error": f"Unknown tool type: {tool}"}

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
