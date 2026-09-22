import asyncio
import json
import os
import re
import difflib
import shutil
import time
from typing import Optional, Dict, Any, Tuple, List

from src.constants import MAX_READ_CHARS, MAX_DIFF_LINES, MAX_OUTPUT_CHARS

_CODENAV_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".cache", "site-packages", ".idea", ".tox",
})
_CODENAV_MAX_HITS = 200
_CODENAV_MAX_LINE = 400
_GREP_TIMEOUT_SECONDS = 20
_GREP_STDERR_PREFIX = 20_000


def _glob_to_regex(pat: str) -> "re.Pattern":
    """Translate a forward-slash glob (**, *, ?) into a compiled regex.
    `**/` matches zero or more complete directories.
    `*` matches within a single path segment (does not cross /).
    """
    i, n, out = 0, len(pat), []
    while i < n:
        if pat[i : i + 3] == "**/":
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out))


def _python_grep_worker(payload: dict, output_queue) -> None:
    """Spawn-safe fallback grep worker used when ripgrep is unavailable.

    Keep this at module scope: a frozen Windows executable cannot safely be
    relaunched as ``sys.executable -c ...``, while multiprocessing can invoke a
    top-level target through its frozen-process bootstrap.
    """
    try:
        flags = re.IGNORECASE if payload["ignore_case"] else 0
        try:
            regex = re.compile(payload["pattern"], flags)
            glob_regex = (
                _glob_to_regex(payload["glob"].replace("\\", "/"))
                if payload["glob"]
                else None
            )
        except re.error as exc:
            output_queue.put(("error", f"grep: bad pattern: {exc}"))
            return

        requested_root = payload["root"]
        skip_dirs = set(payload["skip_dirs"])
        sensitive = {name.casefold() for name in payload["sensitive_names"]}
        max_hits = payload["max_hits"]
        hits = 0

        def within(path: str, root: str) -> bool:
            try:
                return os.path.commonpath(
                    [os.path.normcase(path), os.path.normcase(root)]
                ) == os.path.normcase(root)
            except ValueError:
                return False

        def safe_file(path: str, target: str) -> Optional[str]:
            if os.path.islink(path):
                return None
            canonical = os.path.realpath(path)
            if not within(canonical, requested_root) or not within(canonical, target):
                return None
            parts = [part.casefold() for part in canonical.split(os.sep)]
            if any(part in sensitive for part in parts):
                return None
            try:
                if not os.path.isfile(canonical) or os.stat(canonical).st_nlink > 1:
                    return None
            except OSError:
                return None
            return canonical

        for target in payload["targets"]:
            if hits >= max_hits:
                break
            if os.path.isfile(target):
                file_iter = iter((target,))
            else:
                def walk_files():
                    for directory, dirnames, filenames in os.walk(
                        target, followlinks=False
                    ):
                        dirnames[:] = [
                            name
                            for name in dirnames
                            if name not in skip_dirs
                            and name.casefold() not in sensitive
                            and not os.path.islink(os.path.join(directory, name))
                        ]
                        for name in filenames:
                            yield os.path.join(directory, name)

                file_iter = walk_files()

            for candidate in file_iter:
                path = safe_file(candidate, target)
                if path is None:
                    continue
                relative = os.path.relpath(path, requested_root).replace(os.sep, "/")
                if glob_regex and not (
                    glob_regex.fullmatch(relative)
                    or glob_regex.fullmatch(os.path.basename(path))
                ):
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="strict") as handle:
                        for number, line in enumerate(handle, 1):
                            if regex.search(line):
                                output_queue.put((
                                    "match",
                                    path,
                                    number,
                                    line.rstrip()[:_CODENAV_MAX_LINE],
                                ))
                                hits += 1
                                if hits >= max_hits:
                                    break
                except (UnicodeDecodeError, OSError):
                    continue
                if hits >= max_hits:
                    break
        output_queue.put(("done",))
    except BaseException as exc:
        try:
            output_queue.put(("error", f"grep: fallback worker failed: {exc}"))
        except BaseException:
            pass

def _unified_diff(old: str, new: str, path: str) -> Optional[Dict[str, Any]]:
    if old == new:
        return None
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

class EditFileTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _resolve_tool_path, _resolve_search_root, _truncate
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
        try:
            path = _resolve_tool_path(raw_path)
        except ValueError as e:
            return {"error": f"edit_file: {e}", "exit_code": 1}
        if old == "":
            return {"error": "edit_file: old_string required (use write_file to create a file)", "exit_code": 1}
        if old == new:
            return {"error": "edit_file: old_string and new_string are identical", "exit_code": 1}

        def _apply():
            """Helper function that performs the actual string replacement and file writing logic."""
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

class ReadFileTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _resolve_tool_path, _resolve_search_root, _truncate
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
            path = _resolve_tool_path(raw_path)
        except ValueError as e:
            return {"error": f"read_file: {e}", "exit_code": 1}
        try:
            def _read():
                if offset > 0 or limit > 0:
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

class WriteFileTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _resolve_tool_path, _resolve_search_root, _truncate
        lines = content.split("\n", 1)
        raw_path = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""
        # Decode JSON-object args (the fenced inline-args shape
        # ```write_file {"path": "...", "content": "..."}```), matching
        # ReadFileTool above. Without this the whole JSON string becomes the
        # path and the file is written under a garbage name. This is the live
        # path: there is no filesystem MCP server, so write_file always runs
        # here via _direct_fallback, not through _build_mcp_args.
        _stripped = content.strip()
        if _stripped.startswith("{"):
            try:
                _a = json.loads(_stripped)
                if isinstance(_a, dict) and "path" in _a:
                    raw_path = str(_a.get("path", "")).strip()
                    body = str(_a.get("content", ""))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        try:
            path = _resolve_tool_path(raw_path)
        except ValueError as e:
            return {"error": f"write_file: {e}", "exit_code": 1}
        try:
            def _write():
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

class ApplyPatchTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Apply a small Codex-style patch using exact context matching.

        This is deliberately stricter than git-apply: if an update hunk's old
        text is not found exactly once, the whole patch is rejected before any
        file is changed. That keeps agent edits reviewable and avoids fuzzy
        corruption when the model patches stale context.
        """
        from src.tool_execution import _resolve_tool_path

        patch_text = content or ""
        stripped = patch_text.strip()
        if stripped.startswith("{"):
            try:
                args = json.loads(stripped)
                if isinstance(args, dict):
                    patch_text = str(args.get("patch_text") or args.get("patchText") or args.get("patch") or "")
            except (json.JSONDecodeError, TypeError):
                pass
        if not patch_text.strip():
            return {"error": "apply_patch: patch_text required", "exit_code": 1}

        try:
            ops = _parse_agent_patch(patch_text)
            if not ops:
                return {"error": "apply_patch: no file operations found", "exit_code": 1}
            prepared = []
            for op in ops:
                path = _resolve_tool_path(op["path"])
                kind = op["kind"]
                if kind == "add":
                    if os.path.exists(path):
                        return {"error": f"apply_patch: {op['path']}: already exists", "exit_code": 1}
                    old = ""
                    new = op["content"]
                elif kind == "delete":
                    if not os.path.isfile(path):
                        return {"error": f"apply_patch: {op['path']}: not found", "exit_code": 1}
                    with open(path, "r", encoding="utf-8") as f:
                        old = f.read()
                    new = ""
                else:
                    if not os.path.isfile(path):
                        return {"error": f"apply_patch: {op['path']}: not found", "exit_code": 1}
                    with open(path, "r", encoding="utf-8") as f:
                        old = f.read()
                    new = _apply_patch_hunks(old, op["hunks"], op["path"])
                prepared.append((kind, path, old, new))

            diffs = []
            for kind, path, old, new in prepared:
                if kind == "delete":
                    os.remove(path)
                else:
                    directory = os.path.dirname(path)
                    if directory:
                        os.makedirs(directory, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new)
                diff = _unified_diff(old, new, path)
                if diff:
                    diffs.append(diff)
        except (ValueError, UnicodeDecodeError, PermissionError, OSError) as e:
            return {"error": f"apply_patch: {e}", "exit_code": 1}

        added = sum(int(d.get("added") or 0) for d in diffs)
        removed = sum(int(d.get("removed") or 0) for d in diffs)
        text_parts = [d.get("text", "") for d in diffs if d.get("text")]
        diff_text = "\n".join(text_parts)
        if len(diff_text.splitlines()) > MAX_DIFF_LINES:
            diff_text = "\n".join(diff_text.splitlines()[:MAX_DIFF_LINES]) + f"\n... diff truncated at {MAX_DIFF_LINES} lines"
        result = {
            "output": f"Applied patch ({len(prepared)} file{'s' if len(prepared) != 1 else ''}, +{added}/-{removed})",
            "exit_code": 0,
        }
        if diffs:
            result["diff"] = {
                "text": diff_text,
                "added": added,
                "removed": removed,
                "new_file": any(d.get("new_file") for d in diffs),
                "file": "patch",
            }
        return result

def _parse_agent_patch(patch_text: str) -> List[Dict[str, Any]]:
    lines = patch_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("patch must start with *** Begin Patch")
    if lines[-1].strip() != "*** End Patch":
        raise ValueError("patch must end with *** End Patch")

    ops: List[Dict[str, Any]] = []
    i = 1
    while i < len(lines) - 1:
        line = lines[i]
        if not line:
            i += 1
            continue
        if line.startswith("*** Add File: "):
            path = line[len("*** Add File: "):].strip()
            body = []
            i += 1
            while i < len(lines) - 1 and not lines[i].startswith("*** "):
                if not lines[i].startswith("+"):
                    raise ValueError(f"add file {path}: every content line must start with +")
                body.append(lines[i][1:])
                i += 1
            ops.append({"kind": "add", "path": path, "content": "\n".join(body) + ("\n" if body else "")})
            continue
        if line.startswith("*** Delete File: "):
            path = line[len("*** Delete File: "):].strip()
            ops.append({"kind": "delete", "path": path})
            i += 1
            continue
        if line.startswith("*** Update File: "):
            path = line[len("*** Update File: "):].strip()
            hunks = []
            current = []
            i += 1
            if i < len(lines) - 1 and lines[i].startswith("*** Move to: "):
                raise ValueError("move operations are not supported")
            while i < len(lines) - 1 and not lines[i].startswith("*** "):
                if lines[i].startswith("@@"):
                    if current:
                        hunks.append(current)
                        current = []
                elif lines[i].startswith((" ", "-", "+")):
                    current.append(lines[i])
                elif lines[i] == "":
                    current.append(" ")
                else:
                    raise ValueError(f"update file {path}: invalid patch line {lines[i]!r}")
                i += 1
            if current:
                hunks.append(current)
            if not hunks:
                raise ValueError(f"update file {path}: no hunks")
            ops.append({"kind": "update", "path": path, "hunks": hunks})
            continue
        raise ValueError(f"unexpected patch line: {line!r}")
    return ops

def _apply_patch_hunks(original: str, hunks: List[List[str]], label: str) -> str:
    updated = original
    for idx, hunk in enumerate(hunks, 1):
        old_lines = []
        new_lines = []
        for line in hunk:
            prefix, body = line[:1], line[1:]
            if prefix in (" ", "-"):
                old_lines.append(body)
            if prefix in (" ", "+"):
                new_lines.append(body)
        old_text = "\n".join(old_lines)
        new_text = "\n".join(new_lines)
        if old_text and old_text in updated:
            occurrences = updated.count(old_text)
            if occurrences != 1:
                raise ValueError(f"{label}: hunk {idx} context matched {occurrences} times")
            updated = updated.replace(old_text, new_text, 1)
        elif old_text + "\n" in updated:
            occurrences = updated.count(old_text + "\n")
            if occurrences != 1:
                raise ValueError(f"{label}: hunk {idx} context matched {occurrences} times")
            updated = updated.replace(old_text + "\n", new_text + "\n", 1)
        else:
            raise ValueError(f"{label}: hunk {idx} context not found")
    return updated

class LsTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import (
            _is_denied_tool_path,
            _resolve_search_root,
            _truncate,
        )
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
            root = _resolve_search_root(raw_path)
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
                        if _is_denied_tool_path(os.path.realpath(entry.path)):
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            size = entry.stat(follow_symlinks=False).st_size if not is_dir else 0
                        except OSError:
                            continue
                        rows.append((is_dir, entry.name, size))
            except (PermissionError, OSError) as _e:
                return None, f"ls: {_e}"
            rows.sort(key=lambda r: (not r[0], r[1].lower()))
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

class GlobTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import (
            _SENSITIVE_BASENAMES,
            _can_traverse_tool_path,
            _is_denied_tool_path,
            _resolve_tool_path,
            _resolve_search_root,
            _truncate,
        )
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
            root = _resolve_search_root(str(args.get("path", "")))
        except ValueError as e:
            return {"error": f"glob: {e}", "exit_code": 1}

        def _glob():
            base = os.path.abspath(root)
            if not os.path.isdir(base):
                return None, f"glob: {root}: not a directory"
            rbase = os.path.realpath(base)
            norm_pat = pattern.replace("\\", "/")
            # Fast path: literal pattern (no wildcards) → direct path lookup.
            if not any(c in norm_pat for c in "*?["):
                cand = os.path.realpath(os.path.join(base, norm_pat))
                # Keep the literal lookup inside the search root. os.path.join
                # lets an absolute pattern (or one containing ../) escape `base`,
                # which would turn glob into an existence/path oracle for
                # arbitrary host files — bypassing the workspace/allowlist
                # confinement that _resolve_search_root applies to the root.
                # An escaping literal falls through to the walk, which only ever
                # yields paths under base.
                nbase = os.path.normcase(rbase)
                try:
                    inside = cand == rbase or os.path.commonpath(
                        [os.path.normcase(cand), nbase]
                    ) == nbase
                except ValueError:
                    inside = False
                # A literal that names a deny-listed sensitive file (.env,
                # .ssh/id_rsa, …) falls through to the walk, which skips it —
                # otherwise glob would surface secret paths that read_file /
                # grep already refuse to touch.
                if inside and os.path.exists(cand) and not _is_denied_tool_path(cand):
                    return [cand], None
                # Literal not at exact path — fall through to walk so
                # e.g. "foo.py" still matches at any depth (like rglob).
            # Compile glob to regex: * stays within one segment, **/ spans dirs.
            regex = _glob_to_regex(norm_pat)
            matched = []
            cap = _CODENAV_MAX_HITS * 5
            try:
                for dp, dns, fns in os.walk(base):
                    if not _can_traverse_tool_path(os.path.realpath(dp)):
                        dns[:] = []
                        continue
                    # Prune skipped dirs before descending (unlike rglob which
                    # descends first then filters — fatal on large node_modules).
                    # Sensitive dirs (.ssh, .gnupg, …) are pruned too so glob
                    # never enumerates the keys/tokens inside them.
                    dns[:] = [
                        d for d in dns
                        if d not in _CODENAV_SKIP_DIRS
                        and d not in _SENSITIVE_BASENAMES
                        and _can_traverse_tool_path(os.path.realpath(os.path.join(dp, d)))
                    ]
                    for name in fns + dns:
                        full = os.path.join(dp, name)
                        rel = os.path.relpath(full, base).replace(os.sep, "/")
                        if regex.fullmatch(rel) or regex.fullmatch(name):
                            # Skip deny-listed sensitive files (.env, id_rsa,
                            # known_hosts, …) the same way grep does.
                            if _is_denied_tool_path(os.path.realpath(full)):
                                continue
                            try:
                                mtime = os.stat(full).st_mtime
                            except OSError:
                                mtime = 0
                            matched.append((mtime, full))
                    if len(matched) > cap:
                        break
            except OSError as _e:
                return None, f"glob: {_e}"
            matched.sort(key=lambda t: t[0], reverse=True)
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

class GrepTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import (
            _SENSITIVE_BASENAMES,
            _SENSITIVE_FILE_PATTERNS,
            _agent_readable_data_subdirs,
            _is_denied_tool_path,
            _is_sensitive_path,
            _path_within,
            _resolve_search_root,
            _truncate,
        )
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
            root = _resolve_search_root(str(args.get("path", "")))
        except ValueError as e:
            return {"error": f"grep: {e}", "exit_code": 1}

        def _grep():
            import multiprocessing
            import queue
            import subprocess
            import threading

            from src.constants import DATA_DIR

            rg = shutil.which("rg")
            real_root = os.path.realpath(root)
            data_dir = os.path.realpath(DATA_DIR)
            spans_state = _path_within(data_dir, real_root)

            def is_top_level_safe(path: str, *, partition_generated: bool) -> bool:
                lexical = os.path.abspath(path)
                if os.path.islink(lexical):
                    return False
                canonical = os.path.realpath(lexical)
                if not _path_within(canonical, real_root):
                    return False
                if partition_generated and os.path.basename(lexical) in _CODENAV_SKIP_DIRS:
                    return False
                if _is_sensitive_path(canonical) or _is_denied_tool_path(canonical):
                    return False
                return True

            def safe_targets() -> tuple[list[str], Optional[str]]:
                candidates: list[tuple[str, bool]] = []
                if not spans_state:
                    # Preserve direct-root compatibility: skip-directory policy
                    # prunes descendants, but an explicitly requested allowed
                    # root named node_modules remains searchable.
                    candidates.append((real_root, False))
                else:
                    current = real_root
                    if current != data_dir:
                        for part in os.path.relpath(data_dir, current).split(os.sep):
                            try:
                                with os.scandir(current) as entries:
                                    for entry in entries:
                                        if entry.name != part:
                                            # Reject a sibling link lexically before
                                            # canonicalizing or treating it as a target.
                                            if entry.is_symlink():
                                                continue
                                            candidates.append((entry.path, True))
                            except OSError as exc:
                                return [], f"grep: {exc}"
                            current = os.path.join(current, part)
                    for readable in _agent_readable_data_subdirs():
                        if (
                            _path_within(readable, data_dir)
                            and _path_within(readable, real_root)
                            and os.path.exists(readable)
                        ):
                            candidates.append((readable, True))

                targets: list[str] = []
                seen: set[str] = set()
                for candidate, partition_generated in candidates:
                    if not is_top_level_safe(
                        candidate, partition_generated=partition_generated
                    ):
                        continue
                    canonical = os.path.realpath(candidate)
                    if canonical not in seen:
                        seen.add(canonical)
                        targets.append(canonical)
                return targets, None

            targets, target_error = safe_targets()
            if target_error:
                return None, target_error

            base = real_root if os.path.isdir(real_root) else os.path.dirname(real_root)
            deadline = time.monotonic() + _GREP_TIMEOUT_SECONDS
            lines: list[str] = []

            def parse_rg_result(raw: str) -> Optional[str]:
                try:
                    record = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    return None
                if record.get("type") != "match":
                    return None
                data = record.get("data") or {}
                path = (data.get("path") or {}).get("text")
                text_value = (data.get("lines") or {}).get("text")
                number = data.get("line_number")
                if not isinstance(path, str) or not isinstance(text_value, str):
                    return None
                absolute = path if os.path.isabs(path) else os.path.join(base, path)
                canonical = os.path.realpath(absolute)
                if not _path_within(canonical, real_root) or _is_denied_tool_path(canonical):
                    return None
                return f"{os.path.abspath(absolute)}:{number}:{text_value.rstrip()[:_CODENAV_MAX_LINE]}"

            def run_rg(cmd: list[str]) -> Optional[str]:
                try:
                    process = subprocess.Popen(
                        cmd,
                        cwd=base,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )
                except Exception as exc:
                    return f"grep: {exc}"
                output: queue.Queue[Optional[str]] = queue.Queue(maxsize=max_hits + 2)
                stderr_prefix: list[str] = []
                stderr_size = 0
                stop_reader = threading.Event()

                def enqueue_stdout(value: Optional[str]) -> bool:
                    # The consumer stops at the result cap or deadline. Never
                    # leave a producer blocked on its bounded queue afterward.
                    while not stop_reader.is_set():
                        try:
                            output.put(value, timeout=0.05)
                            return True
                        except queue.Full:
                            continue
                    return False

                def read_stdout() -> None:
                    assert process.stdout is not None
                    try:
                        for line in process.stdout:
                            if not enqueue_stdout(line.rstrip("\n")):
                                break
                    finally:
                        enqueue_stdout(None)

                def read_stderr() -> None:
                    nonlocal stderr_size
                    assert process.stderr is not None
                    while True:
                        chunk = process.stderr.read(4096)
                        if not chunk:
                            break
                        if stderr_size < _GREP_STDERR_PREFIX:
                            kept = chunk[:_GREP_STDERR_PREFIX - stderr_size]
                            stderr_prefix.append(kept)
                            stderr_size += len(kept)

                stdout_thread = threading.Thread(target=read_stdout, daemon=True)
                stderr_thread = threading.Thread(target=read_stderr, daemon=True)
                stdout_thread.start()
                stderr_thread.start()
                timed_out = False
                capped = False
                try:
                    while len(lines) < max_hits:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            break
                        try:
                            raw = output.get(timeout=remaining)
                        except queue.Empty:
                            timed_out = True
                            break
                        if raw is None:
                            break
                        parsed = parse_rg_result(raw)
                        if parsed and parsed not in lines:
                            lines.append(parsed)
                    capped = len(lines) >= max_hits
                finally:
                    stop_reader.set()
                    if (timed_out or capped) and process.poll() is None:
                        process.terminate()
                    try:
                        remaining = max(0.01, deadline - time.monotonic())
                        return_code = process.wait(timeout=min(1, remaining))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        return_code = process.wait()
                    stdout_thread.join()
                    stderr_thread.join()
                if timed_out:
                    return "grep: timed out"
                if not capped and return_code not in (0, 1):
                    detail = "".join(stderr_prefix).strip()
                    return f"grep: {detail or f'process exited {return_code}'}"
                return None

            if rg:
                # Validate even when policy filtering leaves no search targets.
                if not targets:
                    error = run_rg([rg, "--json", "--no-config", "--regexp", pattern])
                    return (None, error) if error else ([], None)
                relative_targets = [os.path.relpath(target, base) for target in targets]
                for offset in range(0, len(relative_targets), 128):
                    if len(lines) >= max_hits:
                        break
                    cmd = [
                        rg, "--json", "--no-config", "--no-follow",
                        "--max-count", str(max_hits - len(lines)),
                        "--max-columns", str(_CODENAV_MAX_LINE),
                        "--max-columns-preview",
                    ]
                    if ignore_case:
                        cmd.append("--ignore-case")
                    if glob_pat:
                        cmd += ["--glob", glob_pat]
                    for sensitive_pattern in _SENSITIVE_FILE_PATTERNS:
                        cmd += ["--iglob", f"!{sensitive_pattern}"]
                    for skipped_dir in _CODENAV_SKIP_DIRS:
                        cmd += ["--glob", f"!**/{skipped_dir}/**"]
                    cmd += ["--regexp", pattern, "--", *relative_targets[offset:offset + 128]]
                    error = run_rg(cmd)
                    if error:
                        return None, error
                return lines, None

            # This runs inside asyncio.to_thread(), so forking would clone a
            # multithreaded process and can deadlock. Spawn is platform-safe and
            # PyInstaller-compatible via launcher's early freeze_support().
            payload = {
                "root": real_root,
                "targets": targets,
                "pattern": pattern,
                "ignore_case": ignore_case,
                "glob": glob_pat,
                "max_hits": max_hits,
                "skip_dirs": tuple(_CODENAV_SKIP_DIRS),
                "sensitive_names": tuple(
                    set(_SENSITIVE_BASENAMES) | set(_SENSITIVE_FILE_PATTERNS)
                ),
            }
            try:
                context = multiprocessing.get_context("spawn")
                output_queue = context.Queue(maxsize=max_hits + 2)
                worker = context.Process(
                    target=_python_grep_worker, args=(payload, output_queue)
                )
                worker.start()
            except Exception as exc:
                try:
                    output_queue.close()
                except (NameError, OSError, ValueError):
                    pass
                return None, f"grep: could not start fallback worker: {exc}"
            error = None
            completed = False
            try:
                while len(lines) < max_hits:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        error = "grep: timed out"
                        break
                    try:
                        # Keep queue waits short enough to observe a spawn
                        # worker that dies during bootstrap/import before it
                        # can enqueue either an error or the done sentinel.
                        record = output_queue.get(timeout=min(0.05, remaining))
                    except queue.Empty:
                        if worker.is_alive():
                            continue
                        worker.join(timeout=0)
                        try:
                            # A multiprocessing queue's feeder can make the
                            # final record visible at process-exit time. Give
                            # that record precedence over the exit status.
                            remaining = deadline - time.monotonic()
                            record = output_queue.get(
                                timeout=min(0.05, max(0, remaining))
                            )
                        except queue.Empty:
                            error = f"grep: fallback worker exited {worker.exitcode}"
                            break
                    if record[0] == "done":
                        completed = True
                        break
                    if record[0] == "error":
                        error = record[1]
                        break
                    _, path, number, text_value = record
                    canonical = os.path.realpath(path)
                    if not _path_within(canonical, real_root) or _is_denied_tool_path(canonical):
                        continue
                    rendered = f"{path}:{number}:{text_value}"
                    if rendered not in lines:
                        lines.append(rendered)
            finally:
                if completed:
                    worker.join(timeout=min(1, max(0.01, deadline - time.monotonic())))
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=1)
                if worker.is_alive():
                    worker.kill()
                    worker.join()
                output_queue.close()
            if error:
                return None, error
            if worker.exitcode not in (0, None) and len(lines) < max_hits:
                return None, f"grep: fallback worker exited {worker.exitcode}"
            return lines, None

        lines, err = await asyncio.to_thread(_grep)
        if err:
            return {"error": err, "exit_code": 1}
        if not lines:
            return {"output": f"No matches for {pattern!r} under {root}", "exit_code": 0}
        out = "\n".join(ln[:_CODENAV_MAX_LINE] for ln in lines)
        if len(lines) >= max_hits:
            out += f"\n... [capped at {max_hits} matches]"
        return {"output": _truncate(out), "exit_code": 0}

class GetWorkspaceTool:
    """Report the active workspace folder (no args). File tools are confined to
    it; the shell starts there (cwd) but is NOT sandboxed."""
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import get_active_workspace
        ws = get_active_workspace()
        if ws:
            return {
                "output": f"{ws}\n(File tools are confined to this folder; the shell starts "
                          f"here but is not sandboxed and can reach outside it.)",
                "exit_code": 0,
            }
        return {
            "output": "No workspace is set. File tools use the default allowed roots; "
                      "resolve paths from the user or use absolute paths.",
            "exit_code": 0,
        }
