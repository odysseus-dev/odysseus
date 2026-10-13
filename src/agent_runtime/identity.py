"""Canonical evidence identities; these helpers never grant filesystem access."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
from .path_policy import _is_sensitive_path


TUI_PYTHON_RUNNER_SETUP = (
    "runner=''; "
    "if [ -x .venv/bin/python ]; then runner=.venv/bin/python; "
    "elif [ -x venv/bin/python ]; then runner=venv/bin/python; "
    "elif git_common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) "
    "&& [ -x \"$(dirname \"$git_common\")/.venv/bin/python\" ]; then "
    "runner=\"$(dirname \"$git_common\")/.venv/bin/python\"; "
    "else runner=python; fi; "
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def artifact_version(value: str, workspace: str) -> str:
    """Content version for a confined declared output; missing/unreadable is explicit."""
    identity = artifact_identity(value, workspace)
    if not workspace or not identity.startswith('workspace:'):
        return 'unobserved'
    root = Path(workspace).resolve()
    candidate = (root / identity.removeprefix('workspace:')).resolve()
    if not candidate.is_relative_to(root) or _is_sensitive_path(str(candidate)):
        return 'unobserved'
    if not hasattr(os, 'O_NOFOLLOW') or not os.supports_dir_fd:
        return 'unobserved'
    directory = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parts = candidate.relative_to(root).parts
        if not parts:
            return 'unobserved'
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
                return 'unobserved'
            result = hashlib.sha256()
            remaining = 64 * 1024 * 1024
            while block := stream.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    return 'unobserved'
                result.update(block)
            return result.hexdigest()
    except (OSError, ValueError):
        return 'missing-or-unreadable'
    finally:
        if directory is not None:
            os.close(directory)


def artifact_identity(value: str, workspace: str = "") -> str:
    """Unify relative, virtual and host aliases without basename matching.

    Resolving symlinks is evidence bookkeeping, never a confinement check. Paths
    outside the workspace retain their absolute identity and cannot satisfy a
    workspace obligation with the same basename.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    path = PurePosixPath(text)
    root = Path(workspace or "/workspace").resolve()
    if path.parts[:2] == ('/', 'workspace'):
        path = PurePosixPath(*path.parts[2:])
    candidate = Path(str(path))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(root)
        return "workspace:" + relative.as_posix()
    except ValueError:
        return "absolute:" + str(candidate.resolve())
    except (OSError, RuntimeError):
        return "unresolved:" + text


def executable_words(command: str) -> tuple[str, ...]:
    """Recognize one foreground command after exact interpreter or cd/set prefixes.

    This is deliberately conservative evidence parsing, not shell authorization.
    Other control flow, substitutions, pipelines and status-masking tails are not proof
    that a verifier returned the recorded shell status.
    """
    text = str(command or '').strip()
    if text.startswith(TUI_PYTHON_RUNNER_SETUP):
        remainder = text[len(TUI_PYTHON_RUNNER_SETUP):]
        # This exact server-owned prelude only selects the interpreter. The
        # trailing command must still be a single foreground invocation whose
        # status is returned unchanged; the generic discovery fallback is not.
        if remainder.startswith('"$runner" '):
            arguments = remainder[len('"$runner" '):]
            if '$' in arguments:
                return ()
            text = 'python ' + arguments
    if any(marker in text for marker in ('`', '$(', '${', '\n', '\r')):
        return ()
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return ()
    while '&&' in words:
        index = words.index('&&')
        prefix = words[:index]
        if not ((len(prefix) == 2 and prefix[0] == 'cd') or prefix == ['set', '-e']):
            return ()
        words = words[index + 1:]
    if any(word and all(c in ';&|<>()' for c in word) for word in words):
        return ()
    while words and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=[^\n]*', words[0]):
        words.pop(0)
    return tuple(words)


def is_test_command(command: str) -> bool:
    words = executable_words(command)
    if not words:
        return False
    if any(word in {'--help', '-h', '--version', '--collect-only', '--co'} for word in words[1:]):
        return False
    binary = Path(words[0]).name
    if binary in {'pytest', 'py.test'}:
        return True
    if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?', binary):
        args = list(words[1:])
        while args and args[0] in {'-I', '-S', '-s', '-E', '-B', '-u'}:
            args.pop(0)
        return len(args) >= 2 and args[:2] in (['-m', 'pytest'], ['-m', 'unittest'])
    if binary in {'npm', 'pnpm', 'yarn', 'make', 'cargo', 'go'}:
        args = words[1:]
        return bool(args and (args[0] == 'test' or binary == 'npm' and args[:2] == ('run', 'test')))
    return bool(re.match(r'^/(?:tests?|verifier)/[^/]+', words[0]))


def is_validation_command(command: str) -> bool:
    words = executable_words(command)
    if not words:
        return False
    binary = Path(words[0]).name
    return (is_test_command(command)
            or binary in {'cat', 'head', 'tail', 'stat', 'wc', 'jq', 'cmp', 'diff',
                          'coqc', 'gcc', 'g++', 'clang', 'clang++', 'javac', 'rustc'}
            or binary == 'test' and len(words) > 1 and words[1] in {'-e', '-f', '-s', '-d'}
            or binary in {'cargo', 'go', 'npm', 'pnpm', 'yarn'} and words[1:2] in {('build',), ('check',)}
            or binary == 'npm' and words[1:3] == ('run', 'build'))
