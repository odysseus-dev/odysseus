"""Existing sensitive-path policy shared by tools and evidence observation.

This is a deny predicate, not an authorization grant or a workspace scope.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from typing import Iterable, Optional

_SENSITIVE_BASENAMES: set[str] = {
    ".ssh", ".gnupg", ".gitconfig",
    ".bashrc", ".bash_profile", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv",
    ".profile", ".tcshrc", ".cshrc", ".env", ".netrc",
}
_SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa",
    "known_hosts", "auth.json", "app.db", "settings.json",
)
_SENSITIVE_BASENAMES_CF = frozenset(b.casefold() for b in _SENSITIVE_BASENAMES)
_SENSITIVE_FILE_PATTERNS_CF = frozenset(p.casefold() for p in _SENSITIVE_FILE_PATTERNS)

# Default sensitive directory boundaries protected from agent writes, edits,
# patches, and mutating shell commands even when no config file exists (#7079).
DEFAULT_PROTECTED_MUTATION_PATHS: tuple[str, ...] = (
    "/etc",
    "~/.ssh",
    "~/.gnupg",
)


def _is_sensitive_path(resolved: str) -> bool:
    # Case folding is required even on POSIX: default macOS volumes are
    # case insensitive but os.path.normcase there does not fold path names.
    parts = [p.casefold() for p in resolved.split(os.sep)]
    filename = parts[-1] if parts else ""
    return any(part in _SENSITIVE_BASENAMES_CF for part in parts) or filename in _SENSITIVE_FILE_PATTERNS_CF


def _sensitive_paths_config_file() -> str:
    override = os.environ.get("ODYSSEUS_SENSITIVE_PATHS_CONFIG", "").strip()
    if override:
        return os.path.expanduser(override)
    try:
        from src.constants import DATA_DIR
        return os.path.join(DATA_DIR, "sensitive_paths.json")
    except Exception:
        return os.path.join("data", "sensitive_paths.json")


def load_sensitive_mutation_config() -> tuple[bool, tuple[str, ...]]:
    """Return ``(enabled, protected_paths)`` for the sensitive-path mutation guard.

    Defaults to enabled with ``("/etc", "~/.ssh", "~/.gnupg")`` when no config
    file is present. A config file may set ``{"enabled": false}`` or supply
    ``extra_paths`` / ``paths`` to protect additional path boundaries.
    """
    cfg_path = _sensitive_paths_config_file()
    enabled = True
    paths: list[str] = list(DEFAULT_PROTECTED_MUTATION_PATHS)
    if cfg_path and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                if "enabled" in data:
                    enabled = bool(data["enabled"])
                if data.get("replace_defaults") is True:
                    paths = []
                for key in ("extra_paths", "additional_paths", "paths"):
                    extra = data.get(key)
                    if isinstance(extra, list):
                        for item in extra:
                            if isinstance(item, str) and item.strip():
                                paths.append(item.strip())
        except (OSError, ValueError, TypeError):
            # Fail closed on unreadable or malformed config files.
            enabled = True
    return enabled, tuple(paths)


def _resolve_with_existing_ancestors(raw_path: str, cwd: Optional[str] = None) -> str:
    """Resolve symlinks across existing ancestor components even if leaf is absent."""
    expanded = os.path.expandvars(os.path.expanduser(raw_path.strip()))
    if not os.path.isabs(expanded):
        base = cwd if cwd else os.getcwd()
        expanded = os.path.join(base, expanded)
    abs_path = os.path.abspath(expanded)
    tail: list[str] = []
    probe = abs_path
    while True:
        if os.path.exists(probe) or os.path.islink(probe):
            real_base = os.path.realpath(probe)
            return os.path.join(real_base, *reversed(tail)) if tail else real_base
        parent = os.path.dirname(probe)
        if parent == probe:
            return abs_path
        tail.append(os.path.basename(probe))
        probe = parent


def _expanded_protected_roots(raw_roots: Iterable[str]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()
    for raw in raw_roots:
        if not raw or not str(raw).strip():
            continue
        text = os.path.expandvars(os.path.expanduser(str(raw).strip()))
        candidates = [os.path.abspath(text), _resolve_with_existing_ancestors(text)]
        if text == "/etc" or text.startswith("/etc/"):
            candidates.append("/private" + text)
        for cand in candidates:
            norm = os.path.normpath(cand)
            key = norm.casefold()
            if key not in seen:
                seen.add(key)
                roots.append(norm)
    return roots


def _is_within_boundary(candidate: str, root: str) -> bool:
    """True iff ``candidate`` equals ``root`` or is a descendant path segment."""
    c_norm = os.path.normpath(candidate).casefold()
    r_norm = os.path.normpath(root).casefold()
    if c_norm == r_norm:
        return True
    try:
        return os.path.commonpath([c_norm, r_norm]) == r_norm
    except ValueError:
        return False


def match_protected_mutation_path(
    raw_path: str,
    *,
    cwd: Optional[str] = None,
    extra_roots: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return the matched protected path boundary if ``raw_path`` targets one, else None."""
    if not raw_path or not isinstance(raw_path, str) or not raw_path.strip():
        return None
    enabled, configured_roots = load_sensitive_mutation_config()
    if not enabled:
        return None

    all_raw_roots = list(configured_roots)
    if extra_roots:
        all_raw_roots.extend(extra_roots)
    roots = _expanded_protected_roots(all_raw_roots)
    if not roots:
        return None

    text = raw_path.strip()
    expanded = os.path.expandvars(os.path.expanduser(text))
    candidates: list[str] = []
    if expanded.startswith("/"):
        candidates.append(os.path.normpath(expanded))
    candidates.append(_resolve_with_existing_ancestors(text, cwd=cwd))

    for cand in candidates:
        for root in roots:
            if _is_within_boundary(cand, root):
                return root
    return None


def check_sensitive_mutation_path(raw_path: str, *, cwd: Optional[str] = None) -> None:
    """Raise ``ValueError`` when ``raw_path`` targets a protected sensitive path."""
    matched = match_protected_mutation_path(raw_path, cwd=cwd)
    if matched is not None:
        raise ValueError(
            f"path {raw_path!r} targets protected sensitive path boundary ({matched})"
        )


# ── Shell mutation pre-check (#7079) ────────────────────────────────────

_WRAPPER_VERBS = frozenset({
    "sudo", "env", "xargs", "nice", "nohup", "time", "timeout",
    "ionice", "chrt", "command", "builtin", "exec",
})

_MUTATION_VERBS = frozenset({
    "rm", "rmdir", "unlink", "mv", "cp", "install", "ln",
    "chmod", "chown", "chgrp", "chattr", "setfacl",
    "touch", "truncate", "dd", "tee", "shred",
    "mkdir", "mktemp", "patch", "rsync", "scp",
})

_SHELL_VERBS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash"})

_REDIR_RE = re.compile(r"^(?:\d+|&)?(?:>>|>\|?|<>)\s*(.*)$")


def _split_shell_segments(command: str) -> list[str]:
    """Split a shell command string into simple command segments on control operators."""
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escape = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\" and not in_single:
            buf.append(ch)
            escape = True
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            if ch in ";\n":
                seg = "".join(buf).strip()
                if seg:
                    segments.append(seg)
                buf.clear()
                i += 1
                continue
            if ch in "&|":
                seg = "".join(buf).strip()
                if seg:
                    segments.append(seg)
                buf.clear()
                if i + 1 < n and command[i + 1] == ch:
                    i += 2
                else:
                    i += 1
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def _extract_command_substitutions(command: str) -> list[str]:
    """Extract inner commands from ``$(...)`` and backtick substitutions."""
    found: list[str] = []
    for match in re.finditer(r"\$\(([^()]+)\)", command):
        inner = match.group(1).strip()
        if inner:
            found.append(inner)
    for match in re.finditer(r"`([^`]+)`", command):
        inner = match.group(1).strip()
        if inner:
            found.append(inner)
    return found


def _tokenize_segment(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _unwrap_command_tokens(tokens: list[str]) -> list[str]:
    """Strip env assignments and wrapper prefixes (sudo, env, xargs, nice, ...)."""
    idx = 0
    n = len(tokens)
    while idx < n:
        tok = tokens[idx]
        if "=" in tok and not tok.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            idx += 1
            continue
        verb = os.path.basename(tok).casefold()
        if verb not in _WRAPPER_VERBS:
            break
        idx += 1
        if verb == "sudo":
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T"} and idx < n:
                    idx += 1
        elif verb == "env":
            while idx < n:
                cur = tokens[idx]
                if cur == "--":
                    idx += 1
                    break
                if cur.startswith("-"):
                    idx += 1
                    if cur in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"} and idx < n:
                        idx += 1
                    continue
                if "=" in cur and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", cur):
                    idx += 1
                    continue
                break
        elif verb == "xargs":
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in {"-n", "-L", "-P", "-I", "-J", "-d", "-E", "-s", "-a"} and idx < n:
                    idx += 1
        elif verb == "nice":
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in {"-n", "--adjustment"} and idx < n:
                    idx += 1
        elif verb == "timeout":
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in {"-k", "-s", "--kill-after", "--signal"} and idx < n:
                    idx += 1
            if idx < n and not tokens[idx].startswith("-"):
                idx += 1
        elif verb in {"ionice", "chrt"}:
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in {"-c", "-n", "-p"} and idx < n:
                    idx += 1
        elif verb in {"command", "exec"}:
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if verb == "exec" and flag == "-a" and idx < n:
                    idx += 1
    return tokens[idx:]


def _extract_redirection_targets(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Separate redirection targets from normal command tokens."""
    redir_targets: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = _REDIR_RE.match(tok)
        if m:
            attached = m.group(1).strip()
            if attached:
                redir_targets.append(attached)
            elif i + 1 < len(tokens):
                redir_targets.append(tokens[i + 1])
                i += 1
            i += 1
            continue
        remaining.append(tok)
        i += 1
    return redir_targets, remaining


def find_sensitive_shell_mutation(command: str, *, cwd: Optional[str] = None) -> Optional[str]:
    """Return a refusal reason if ``command`` mutates a protected sensitive path."""
    if not command or not isinstance(command, str):
        return None
    enabled, _ = load_sensitive_mutation_config()
    if not enabled:
        return None

    queue = [command]
    queue.extend(_extract_command_substitutions(command))
    seen: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)

        prev_pipeline_tokens: list[str] = []
        for segment in _split_shell_segments(current):
            raw_tokens = _tokenize_segment(segment)
            if not raw_tokens:
                continue
            current_pipeline_tokens = prev_pipeline_tokens
            redir_targets, cmd_tokens = _extract_redirection_targets(raw_tokens)
            prev_pipeline_tokens = cmd_tokens
            for target in redir_targets:
                matched = match_protected_mutation_path(target, cwd=cwd)
                if matched is not None:
                    return (
                        f"shell redirection target {target!r} points inside "
                        f"protected sensitive path ({matched})"
                    )

            unwrapped = _unwrap_command_tokens(cmd_tokens)
            if not unwrapped:
                continue
            verb = os.path.basename(unwrapped[0]).casefold()
            args = unwrapped[1:]

            if verb in _SHELL_VERBS:
                for idx, arg in enumerate(args):
                    if arg in {"-c", "-lc", "-cl"} and idx + 1 < len(args):
                        queue.append(args[idx + 1])
                continue

            if verb in {"sed", "perl"}:
                in_place = any(
                    a == "-i" or a.startswith("-i") or a.startswith("--in-place")
                    for a in args
                )
                if in_place:
                    for arg in args:
                        if arg.startswith("-"):
                            continue
                        matched = match_protected_mutation_path(arg, cwd=cwd)
                        if matched is not None:
                            return (
                                f"{verb} -i targets protected sensitive path "
                                f"{arg!r} ({matched})"
                            )
                continue

            if verb in {"tar", "unzip"}:
                dest_flags = {"-C", "--directory"} if verb == "tar" else {"-d"}
                for idx, arg in enumerate(args):
                    if arg in dest_flags and idx + 1 < len(args):
                        matched = match_protected_mutation_path(args[idx + 1], cwd=cwd)
                        if matched is not None:
                            return (
                                f"{verb} destination {args[idx + 1]!r} targets "
                                f"protected sensitive path ({matched})"
                            )
                continue

            if verb == "dd":
                for arg in args:
                    if arg.startswith("of="):
                        target = arg[3:]
                        matched = match_protected_mutation_path(target, cwd=cwd)
                        if matched is not None:
                            return (
                                f"dd output {target!r} targets protected sensitive "
                                f"path ({matched})"
                            )
                continue

            if verb in {"python", "python3", "node", "ruby"}:
                for idx, arg in enumerate(args):
                    if arg in {"-c", "-e"} and idx + 1 < len(args):
                        snippet = args[idx + 1]
                        for token in re.findall(r"""['"]([^'"]+)['"]""", snippet):
                            matched = match_protected_mutation_path(token, cwd=cwd)
                            if matched is not None:
                                return (
                                    f"{verb} inline script references protected "
                                    f"sensitive path {token!r} ({matched})"
                                )
                continue

            if verb not in _MUTATION_VERBS:
                continue

            # Check if unwrapped command came via `xargs` fed by a preceding pipeline stage
            unwrapped_via_xargs = any(
                os.path.basename(t).casefold() == "xargs" for t in cmd_tokens
            )
            candidate_args = list(args)
            if unwrapped_via_xargs and current_pipeline_tokens:
                candidate_args.extend(current_pipeline_tokens[1:])

            for arg in candidate_args:
                if arg == "--" or arg.startswith("-"):
                    continue
                raw_pieces = re.split(r"(?:\\n|\\0|\\r|[\r\n\x00])+", arg) if unwrapped_via_xargs else [arg]
                for piece in raw_pieces:
                    piece = piece.strip()
                    if not piece:
                        continue
                    matched = match_protected_mutation_path(piece, cwd=cwd)
                    if matched is not None:
                        return (
                            f"{verb} targets protected sensitive path "
                            f"{piece!r} ({matched})"
                        )


    return None
