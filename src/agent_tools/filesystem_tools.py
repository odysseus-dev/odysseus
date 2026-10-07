import asyncio
import json
import os
import re
import difflib
import secrets
import shutil
import time
import tempfile
from typing import Optional, Dict, Any, Tuple, List

from src.constants import MAX_READ_CHARS, MAX_DIFF_LINES, MAX_OUTPUT_CHARS
from src.path_confinement import is_inside

_CODENAV_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".cache", "site-packages", ".idea", ".tox",
})
_CODENAV_MAX_HITS = 200
_CODENAV_MAX_LINE = 400
_GREP_TIMEOUT_SECONDS = 20
_GREP_STDERR_PREFIX = 20_000
_STRUCTURED_DOCUMENT_SUFFIXES = frozenset({
    ".doc", ".docx", ".epub", ".pdf", ".pptx", ".xls", ".xlsx",
})
_BINARY_ARTIFACT_SUFFIXES = _STRUCTURED_DOCUMENT_SUFFIXES | frozenset({
    ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".mp3", ".mp4", ".ogg",
    ".png", ".wav", ".webm", ".webp", ".zip",
})


def _visible_bound_resource(path):
    from src.agent_runtime.resource_binding import active_resource_operation
    bound = active_resource_operation()
    if bound is None:
        return True
    try:
        bound.resolve_path(path)
        return True
    except (ValueError, OSError, RuntimeError):
        return False

# Models frequently put source artifacts in a Markdown code fence even when a
# tool schema asks for the raw file body. Persisting that fence makes HTML,
# CSS, JavaScript, and source files invalid. Restrict normalization to
# code-like targets so a user can still write a literal fence to Markdown.
_FENCED_SOURCE_SUFFIXES = frozenset({
    ".css", ".csv", ".html", ".htm", ".js", ".json", ".jsx", ".mjs",
    ".py", ".sh", ".sql", ".svg", ".ts", ".tsx", ".xml", ".yaml", ".yml",
})


def _unwrap_fenced_source_body(body: str, path: str) -> str:
    """Remove an accidental outer Markdown fence from a source artifact.

    An opening fence is enough to normalize: generation can end during a tool
    call while its argument remains otherwise usable, and retaining the fence
    corrupts the artifact. This only applies to source-like file extensions.
    """
    if os.path.splitext(path)[1].casefold() not in _FENCED_SOURCE_SUFFIXES:
        return body
    match = re.match(r"^(\s*)```[^\r\n]*\r?\n", body)
    if not match:
        return body
    unwrapped = body[match.end():]
    return re.sub(r"\r?\n```\s*$", "", unwrapped)


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


def _validate_grep_descriptor(descriptor):
    """Revalidate an inert parent snapshot without inherited ContextVars."""
    path, identity, ancestors = descriptor
    if os.path.islink(path) or os.path.realpath(path) != path:
        raise ValueError("grep: resource path changed")
    for parent, observed in ancestors:
        info = os.stat(parent, follow_symlinks=False)
        if not os.path.isdir(parent) or (info.st_dev, info.st_ino) != tuple(observed):
            raise ValueError("grep: resource ancestor changed")
    info = os.stat(path, follow_symlinks=False)
    import stat
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
            or (info.st_dev, info.st_ino) != tuple(identity)):
        raise ValueError("grep: resource identity changed")


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

        max_hits = payload["max_hits"]
        hits = 0

        for descriptor in payload["files"]:
            if hits >= max_hits:
                break
            path, identity, ancestors = descriptor
            _validate_grep_descriptor(descriptor)
            relative = os.path.relpath(path, payload["base"]).replace(os.sep, "/")
            if glob_regex and not (
                glob_regex.fullmatch(relative) or glob_regex.fullmatch(os.path.basename(path))
            ):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="strict") as handle:
                    info = os.fstat(handle.fileno())
                    if (info.st_dev, info.st_ino) != tuple(identity) or info.st_nlink > 1:
                        raise ValueError("grep: resource identity changed before read")
                    for number, line in enumerate(handle, 1):
                        if regex.search(line):
                            output_queue.put(("match", path, number, line.rstrip()[:_CODENAV_MAX_LINE]))
                            hits += 1
                            if hits >= max_hits:
                                break
            except UnicodeDecodeError:
                continue
            except OSError as error:
                output_queue.put(("error", f"grep: {error}"))
                return
        output_queue.put(("done",))
    except BaseException as exc:
        try:
            output_queue.put(("error", f"grep: fallback worker failed: {exc}"))
        except BaseException:
            pass

def _normalize_eol(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_ending(text: str) -> str:
    """The most common ending, with LF chosen on a tie or an empty file."""
    crlf = text.count("\r\n")
    counts = {"\n": text.count("\n") - crlf, "\r\n": crlf, "\r": text.count("\r") - crlf}
    return max(counts, key=counts.get)


def _write_file_text(original: str, body: str) -> str:
    """Exact write_file post-state, shared with its effect adapter."""
    if "\n" not in original and "\r" not in original:
        return body
    return _normalize_eol(body).replace("\n", _line_ending(original))


def _patch_file_text(original: str, hunks: List[List[str]], label: str) -> str:
    """Match LF patch context and restore the target's predominant ending."""
    updated = _apply_patch_hunks(_normalize_eol(original), hunks, label)
    return updated.replace("\n", _line_ending(original))


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

def _edit_file_text(original: str, old: str, new: str, replace_all: bool) -> tuple[str | None, str]:
    """The exact text edit_file writes for ``original``, or None and why not.

    Pure: the effect adapter derives the requested post-state from this same
    function, so the postcondition is the producer's own transformation.
    """
    count = original.count(old)
    if count == 0:
        return None, "not_found"
    if count > 1 and not replace_all:
        return None, f"not_unique:{count}"
    return (original.replace(old, new) if replace_all else original.replace(old, new, 1)), "ok"


class EditFileTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _resolve_tool_path, _resolve_search_root, _truncate
        try:
            args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            return {"error": "edit_file: expected valid JSON arguments", "exit_code": 1}
        if not isinstance(args, dict):
            return {"error": "edit_file: expected a JSON object", "exit_code": 1}
        raw_path_value = args.get("path")
        raw_path = raw_path_value.strip() if isinstance(raw_path_value, str) else ""
        old = args.get("old_string")
        new = args.get("new_string")
        replace_all = args.get("replace_all", False)
        if not raw_path:
            return {"error": "edit_file: path required", "exit_code": 1}
        if not isinstance(old, str) or not old:
            return {"error": "edit_file: old_string required (use write_file to create a file)", "exit_code": 1}
        if not isinstance(new, str):
            return {"error": "edit_file: new_string required", "exit_code": 1}
        if not isinstance(replace_all, bool):
            return {"error": "edit_file: replace_all must be a boolean", "exit_code": 1}
        try:
            path = _resolve_tool_path(raw_path)
        except ValueError as e:
            return {"error": f"edit_file: {e}", "exit_code": 1}
        if old == new:
            return {"error": "edit_file: old_string and new_string are identical", "exit_code": 1}

        def _apply():
            """Helper function that performs the actual string replacement and file writing logic."""
            # Exact replacement must not normalize unrelated CRLF/CR newlines.
            with open(path, "r", encoding="utf-8", newline="") as f:
                original = f.read()
            updated, status = _edit_file_text(original, old, new, replace_all)
            if updated is None:
                return original, None, status
            attempted.append(True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(updated)
            return original, updated, "ok"

        # In-place rewrite: a failure after truncation may leave partial bytes.
        attempted = []
        partial = lambda: {"mutation_attempted": True} if attempted else {}
        try:
            original, updated, status = await asyncio.to_thread(_apply)
        except FileNotFoundError:
            return {"error": f"edit_file: {path}: not found (use write_file to create it)", "exit_code": 1, **partial()}
        except (IsADirectoryError, UnicodeDecodeError):
            return {"error": f"edit_file: {path}: not an editable text file", "exit_code": 1, **partial()}
        except PermissionError:
            return {"error": f"edit_file: {path}: permission denied", "exit_code": 1, **partial()}
        except OSError as e:
            return {"error": f"edit_file: {path}: {e}", "exit_code": 1, **partial()}

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
                if not isinstance(_a, dict):
                    return {"error": "read_file: expected a JSON object", "exit_code": 1}
                raw_path_value = _a.get("path")
                raw_path = raw_path_value.strip() if isinstance(raw_path_value, str) else ""
                offset = int(_a.get("offset") or 0)
                limit = int(_a.get("limit") or 0)
            except (json.JSONDecodeError, TypeError, ValueError):
                return {"error": "read_file: expected valid JSON arguments", "exit_code": 1}
        if not raw_path:
            return {"error": "read_file: path required", "exit_code": 1}
        try:
            path = _resolve_tool_path(raw_path)
        except ValueError as e:
            return {"error": f"read_file: {e}", "exit_code": 1}
        try:
            def _read():
                if os.path.splitext(path)[1].lower() in _STRUCTURED_DOCUMENT_SUFFIXES:
                    from src.document_processor import extract_local_document

                    extracted = extract_local_document(
                        path,
                        display_name=os.path.basename(path),
                        analyze_embedded_images=False,
                    )
                    if offset > 0 or limit > 0:
                        lines = extracted.splitlines(keepends=True)
                        start = max(offset, 1) - 1
                        stop = start + limit if limit > 0 else None
                        return "".join(lines[start:stop])[:MAX_READ_CHARS]
                    return extracted[:MAX_READ_CHARS + 1]
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


def _write_new_file_without_overwrite(path: str, body: str) -> None:
    """Publish a new file without exposing a writable placeholder at its path.

    Stage beside the destination, then hard-link it into place. The link is
    atomic and fails if another writer created the destination first.
    """
    directory = os.path.dirname(path) or "."
    temporary_path = os.path.join(
        directory, f".odysseus-write-{secrets.token_hex(16)}.tmp"
    )
    fd = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o666,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temporary_file:
            fd = None
            temporary_file.write(body)
        os.link(temporary_path, path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


class _EmptyBodyWouldTruncate(Exception):
    """Raised inside the write thread when an undeclared empty body is about to
    replace a file that holds bytes. Carries the size at risk so the caller can be
    told what it would have lost (#6414)."""

    def __init__(self, path: str, existing_bytes: int):
        super().__init__(path)
        self.path = path
        self.existing_bytes = existing_bytes

def _parse_write_intent(content: str) -> tuple[str, str, bool, bool]:
    """Classify the original transport, before binding or source normalization.

    The returned clear flag is derived here, never from a caller-supplied key.
    """
    if not isinstance(content, str):
        raise ValueError("write_file: expected string arguments")
    if content.lstrip().startswith("{"):
        try:
            args = json.loads(content)
        except (TypeError, ValueError) as error:
            raise ValueError("write_file: expected valid JSON arguments") from error
        if not isinstance(args, dict):
            raise ValueError("write_file: expected a JSON object")
        path, body = args.get("path"), args.get("content")
        if not isinstance(body, str):
            raise ValueError("write_file: content required and must be a string")
        if not isinstance(path, str):
            raise ValueError("write_file: path required and must be a string")
        return path.strip(), body, True, not body.strip()
    path, delimiter, body = content.partition("\n")
    return path.strip(), body, bool(delimiter), False


class WriteFileTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _display_tool_path, _resolve_tool_path
        from src.agent_runtime.resource_binding import active_resource_operation
        bound = active_resource_operation()
        original = bound.operation.input if bound is not None else content
        try:
            _, _, has_section, declared_clear = _parse_write_intent(original)
            raw_path, body, _, _ = _parse_write_intent(content)
        except ValueError as error:
            return {"error": str(error), "exit_code": 1}
        if not raw_path:
            return {"error": "write_file: path required", "exit_code": 1}
        try:
            path = _resolve_tool_path(raw_path)
        except ValueError as e:
            return {"error": f"write_file: {e}", "exit_code": 1}
        body = _unwrap_fenced_source_body(body, path)
        # A frequent multimodal artifact failure is writing SVG markup to a
        # path whose extension promises a raster image. The file exists, so
        # ordinary artifact checks pass, but image judges cannot decode it.
        # Reject the mismatch with an actionable native-tool recovery path:
        # save the SVG with an .svg suffix, then use inspect_media to render
        # it to the requested PNG/JPEG path.
        image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        body_probe = body.lstrip().casefold()
        if os.path.splitext(path)[1].casefold() in image_suffixes and (
            body_probe.startswith("<svg")
            or (body_probe.startswith("<?xml") and "<svg" in body_probe[:2000])
        ):
            return {
                "error": (
                    f"write_file: {path} contains SVG markup but has a raster "
                    "image extension. Write the SVG to a .svg path first, "
                    "then call inspect_media with that SVG as path and this "
                    "path as output_path to render a real raster image."
                ),
                "exit_code": 1,
                "artifact_format_error": True,
            }
        # write_file is a UTF-8 text writer. Refuse to silently destroy an
        # existing PDF, image, archive, or media artifact produced by a
        # format-aware tool, especially after the agent has verified it.
        suffix = os.path.splitext(path)[1].casefold()
        if suffix in _BINARY_ARTIFACT_SUFFIXES:
            target_existed = os.path.isfile(path)
            return {
                "error": (
                    f"write_file: refusing UTF-8 text for binary artifact path {path}. "
                    "Use Python or a format-specific creation tool, then inspect the result."
                ),
                "exit_code": 1,
                "binary_artifact_preserved": target_existed,
            }
        if not has_section:
            return {"error": "write_file: content required; missing content section", "exit_code": 1}
        if raw_path.endswith(("/", "\\")) or os.path.isdir(path):
            return {"error": "write_file: target is a directory", "exit_code": 1}
        # This writer truncates in place. Once that stage is reached, a failure
        # may leave a partial file; report it so effect evidence stays honest.
        attempted = []
        try:
            def _write():
                old = ""
                try:
                    with open(path, "r", encoding="utf-8", newline="") as f:
                        old = f.read()
                except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError):
                    old = ""
                d = os.path.dirname(path)
                if d:
                    os.makedirs(d, exist_ok=True)
                if not body.strip() and not declared_clear:
                    # Empty/whitespace-only writes to an already-empty file are no-ops.
                    # Avoid reopening in truncating mode: another writer may have added
                    # data since the read above.
                    if os.path.isfile(path):
                        existing_bytes = os.path.getsize(path)
                        if existing_bytes > 0:
                            raise _EmptyBodyWouldTruncate(path, existing_bytes)
                        return old, 0

                    try:
                        if body:
                            # Publish whitespace content atomically. Writing it
                            # after exclusive creation could overwrite bytes from
                            # a writer that filled the new placeholder meanwhile.
                            attempted.append(True)
                            _write_new_file_without_overwrite(path, body)
                        else:
                            # An exact empty body needs no staged data, so create
                            # the file exclusively and never write through it.
                            attempted.append(True)
                            with open(path, "x", encoding="utf-8"):
                                pass
                    except FileExistsError:
                        if os.path.isfile(path):
                            existing_bytes = os.path.getsize(path)
                            if existing_bytes > 0:
                                raise _EmptyBodyWouldTruncate(path, existing_bytes)
                            return old, 0
                        raise
                    return old, len(body.encode("utf-8"))

                committed_body = _write_file_text(old, body)
                attempted.append(True)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(committed_body)
                return old, len(committed_body.encode("utf-8"))
            old_content, size = await asyncio.to_thread(_write)
        except _EmptyBodyWouldTruncate as e:
            clear_call = json.dumps({"path": raw_path, "content": ""})
            return {
                "error": (
                    f"write_file: refused to write an empty body over {e.path} — it holds "
                    f"{e.existing_bytes} bytes, which the write would have destroyed, so "
                    f"the file is unchanged. To clear it on purpose, resend with an "
                    f"explicit empty content: {clear_call}"
                ),
                "exit_code": 1,
            }
        except PermissionError:
            return {"error": f"write_file: {path}: permission denied", "exit_code": 1,
                    **({"mutation_attempted": True} if attempted else {})}
        except OSError as e:
            return {"error": f"write_file: {path}: {e}", "exit_code": 1,
                    **({"mutation_attempted": True} if attempted else {})}
        committed_body = (old_content if size == 0 and not declared_clear and not body.strip()
                          else _write_file_text(old_content, body))
        diff = _unified_diff(old_content, committed_body, path)
        result = {
            "output": (f"Wrote {size} bytes to {_display_tool_path(path)}" if attempted else
                       f"No write performed for {_display_tool_path(path)} (implicit empty body)"),
            "exit_code": 0,
            **({"write_noop": True} if not attempted else {}),
        }
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
                    with open(path, "r", encoding="utf-8", newline="") as f:
                        old = f.read()
                    new = ""
                else:
                    if not os.path.isfile(path):
                        return {"error": f"apply_patch: {op['path']}: not found", "exit_code": 1}
                    with open(path, "r", encoding="utf-8", newline="") as f:
                        old = f.read()
                    new = _patch_file_text(old, op["hunks"], op["path"])
                prepared.append((kind, path, old, new))

            staged: list[tuple[str, str]] = []
            backups: list[tuple[str, str | None]] = []
            try:
                for kind, path, _old, new in prepared:
                    if kind == "delete":
                        continue
                    directory = os.path.dirname(path) or "."
                    os.makedirs(directory, exist_ok=True)
                    fd, temp_path = tempfile.mkstemp(
                        prefix=f".{os.path.basename(path)}.odysseus-",
                        dir=directory,
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                            handle.write(new)
                            handle.flush()
                            os.fsync(handle.fileno())
                        if os.path.exists(path):
                            shutil.copymode(path, temp_path)
                    except BaseException:
                        try:
                            os.unlink(temp_path)
                        except OSError:
                            pass
                        raise
                    staged.append((path, temp_path))

                for _kind, path, _old, _new in prepared:
                    if os.path.exists(path):
                        directory = os.path.dirname(path) or "."
                        fd, backup_path = tempfile.mkstemp(
                            prefix=f".{os.path.basename(path)}.odysseus-backup-",
                            dir=directory,
                        )
                        os.close(fd)
                        os.unlink(backup_path)
                        os.replace(path, backup_path)
                        backups.append((path, backup_path))
                    else:
                        backups.append((path, None))

                staged_by_path = dict(staged)
                for kind, path, _old, _new in prepared:
                    if kind != "delete":
                        os.replace(staged_by_path[path], path)
                staged.clear()
            except BaseException:
                for path, backup_path in reversed(backups):
                    try:
                        if os.path.exists(path):
                            os.unlink(path)
                        if backup_path and os.path.exists(backup_path):
                            os.replace(backup_path, path)
                    except OSError:
                        pass
                raise
            finally:
                for _path, temp_path in staged:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                for _path, backup_path in backups:
                    if backup_path:
                        try:
                            os.unlink(backup_path)
                        except OSError:
                            pass

            diffs = []
            for _kind, path, old, new in prepared:
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
        from src.tool_execution import _display_tool_path, _is_denied_tool_path, _resolve_search_root, _truncate
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
                        if _is_denied_tool_path(os.path.realpath(entry.path)) or not _visible_bound_resource(entry.path):
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
            lines = [f"{_display_tool_path(root)}:"]
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
            _display_tool_path,
            _is_sensitive_path,
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
                inside = is_inside(rbase, cand)
                # A literal that names a deny-listed sensitive file (.env,
                # .ssh/id_rsa, …) falls through to the walk, which skips it —
                # otherwise glob would surface secret paths that read_file /
                # grep already refuse to touch.
                if inside and os.path.exists(cand) and not _is_denied_tool_path(cand) and _visible_bound_resource(cand):
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
                            if _is_denied_tool_path(os.path.realpath(full)) or not _visible_bound_resource(full):
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
            return {"output": f"No files matching {pattern!r} under {_display_tool_path(root)}", "exit_code": 0}
        out = "\n".join(_display_tool_path(path) for path in paths)
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
            _display_tool_path,
            _can_traverse_tool_path,
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
            deadline = time.monotonic() + _GREP_TIMEOUT_SECONDS
            base = real_root if os.path.isdir(real_root) else os.path.dirname(real_root)
            from src.agent_runtime.resource_binding import active_resource_operation
            bound = active_resource_operation()
            if bound is not None:
                bound.validate()
            files = []

            def check_deadline():
                if time.monotonic() >= deadline:
                    raise TimeoutError("grep: timed out")

            def observe_file(path):
                check_deadline()
                if os.path.islink(path):
                    return
                canonical = os.path.realpath(path)
                if not _path_within(canonical, base) or _is_denied_tool_path(canonical):
                    return
                if bound is not None:
                    try:
                        bound.resolve_path(canonical)
                    except ValueError:
                        # Intentionally denied publication control-plane paths
                        # are omitted before any producer is allowed to read.
                        return
                info = os.stat(canonical, follow_symlinks=False)
                if not os.path.isfile(canonical):
                    return
                ancestors = []
                parent = os.path.dirname(canonical)
                while _path_within(parent, base):
                    observed = os.stat(parent, follow_symlinks=False)
                    ancestors.append((parent, (observed.st_dev, observed.st_ino)))
                    if parent == base:
                        break
                    parent = os.path.dirname(parent)
                files.append((canonical, (info.st_dev, info.st_ino), tuple(ancestors)))
                if len(files) > 100_000:
                    raise ValueError("grep: enumeration limit exceeded; scan incomplete")

            try:
                if os.path.islink(root):
                    raise ValueError("grep: symlink search root is not allowed")
                if os.path.isfile(real_root):
                    observe_file(real_root)
                elif os.path.isdir(real_root):
                    pending_directories = [real_root]
                    enumerated = 0
                    while pending_directories:
                        check_deadline()
                        directory = pending_directories.pop()
                        if not _can_traverse_tool_path(directory):
                            continue
                        with os.scandir(directory) as entries:
                            for entry in entries:
                                check_deadline()
                                enumerated += 1
                                if enumerated > 100_000:
                                    raise ValueError("grep: enumeration limit exceeded; scan incomplete")
                                if entry.is_symlink():
                                    continue
                                canonical = os.path.realpath(entry.path)
                                if not _path_within(canonical, base):
                                    raise ValueError("grep: directory identity changed during enumeration")
                                if entry.is_dir(follow_symlinks=False):
                                    if (entry.name not in _CODENAV_SKIP_DIRS
                                            and _can_traverse_tool_path(canonical)
                                            and (bound is None or _is_denied_tool_path(canonical)
                                                 or _visible_bound_resource(canonical))):
                                        pending_directories.append(canonical)
                                else:
                                    observe_file(entry.path)
                else:
                    raise FileNotFoundError(f"grep: {root}: not found")
                check_deadline()
            except (OSError, ValueError) as error:
                return None, str(error) if str(error).startswith("grep:") else f"grep: {error}"
            descriptors = {record[0]: record for record in files}
            targets = list(descriptors)
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
                if canonical not in descriptors:
                    raise ValueError("grep: producer returned an undeclared resource")
                _validate_grep_descriptor(descriptors[canonical])
                if bound is not None:
                    bound.resolve_path(canonical)
                return f"{_display_tool_path(canonical)}:{number}:{text_value.rstrip()[:_CODENAV_MAX_LINE]}"

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
                if glob_pat:
                    try:
                        glob_regex = _glob_to_regex(glob_pat.replace("\\", "/"))
                        targets = [
                            target for target in targets
                            if glob_regex.fullmatch(os.path.relpath(target, base).replace(os.sep, "/"))
                            or glob_regex.fullmatch(os.path.basename(target))
                        ]
                    except re.error:
                        pass
                # Validate even when policy filtering leaves no search targets.
                if not targets:
                    cmd_probe = [rg, "--json", "--no-config"]
                    if glob_pat:
                        cmd_probe += ["--glob", glob_pat]
                    cmd_probe += ["--regexp", pattern, "--", "-"]
                    error = run_rg(cmd_probe)
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
                    try:
                        for path in targets[offset:offset + 128]:
                            check_deadline()
                            _validate_grep_descriptor(descriptors[path])
                            if bound is not None:
                                bound.resolve_path(path)
                        error = run_rg(cmd)
                    except (OSError, ValueError) as exc:
                        return None, f"grep: {exc}"
                    if error:
                        return None, error
                return lines, None

            # This runs inside asyncio.to_thread(), so forking would clone a
            # multithreaded process and can deadlock. Spawn is platform-safe and
            # PyInstaller-compatible via launcher's early freeze_support().
            payload = {
                "root": base,
                "base": base,
                "files": tuple(files),
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
                    if canonical not in descriptors:
                        error = "grep: fallback returned an undeclared resource"
                        break
                    try:
                        _validate_grep_descriptor(descriptors[canonical])
                        if bound is not None:
                            bound.resolve_path(canonical)
                    except (OSError, ValueError) as exc:
                        error = f"grep: {exc}"
                        break
                    rendered = f"{_display_tool_path(canonical)}:{number}:{text_value}"
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
            if (not completed or worker.exitcode not in (0, None)) and len(lines) < max_hits:
                return None, f"grep: fallback worker exited {worker.exitcode}"
            return lines, None

        lines, err = await asyncio.to_thread(_grep)
        if err:
            return {"error": err, "exit_code": 1}
        if not lines:
            return {"output": f"No matches for {pattern!r} under {_display_tool_path(root)}", "exit_code": 0}
        physical_root = os.path.realpath(root)
        display_root = _display_tool_path(physical_root)
        out = "\n".join(
            (display_root + ln[len(physical_root):] if ln.startswith(physical_root) else ln)[:_CODENAV_MAX_LINE]
            for ln in lines
        )
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
                "output": "/workspace\n(File tools are confined to this folder; the shell starts "
                          f"here but is not sandboxed and can reach outside it.)",
                "exit_code": 0,
            }
        return {
            "output": "No workspace is set. File tools use the default allowed roots; "
                      "resolve paths from the user or use absolute paths.",
            "exit_code": 0,
        }
