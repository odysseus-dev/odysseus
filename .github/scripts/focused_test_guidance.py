#!/usr/bin/env python3
"""Report focused pytest guidance for changed paths under tests/."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Iterable
from pathlib import PurePosixPath


def parse_paths(raw_paths: bytes) -> list[str]:
    """Decode the NUL-delimited output of ``git diff --name-only -z``."""
    return [os.fsdecode(path) for path in raw_paths.split(b"\0") if path]


def changed_paths_from_merge_base(base_sha: str, head_sha: str) -> list[str]:
    """Return changed ``tests/`` paths using GitHub PR three-dot semantics.

    GitHub PR changed files are based on the merge base and the PR head, not a
    direct endpoint diff between the current base branch tip and the PR head.
    Using the direct endpoint diff can include files changed only on the base
    branch when the PR branch is stale.
    """
    merge_base = subprocess.check_output(
        ["git", "merge-base", base_sha, head_sha],
        stderr=subprocess.DEVNULL,
    ).strip()
    raw_paths = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            "-z",
            os.fsdecode(merge_base),
            head_sha,
            "--",
            "tests/",
        ],
    )
    return parse_paths(raw_paths)


def select_test_paths(paths: Iterable[str]) -> list[str]:
    """Return unique, repository-relative paths contained by tests/."""
    selected: set[str] = set()
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts:
            continue
        parts = tuple(part for part in path.parts if part != ".")
        if len(parts) >= 2 and parts[0] == "tests":
            selected.add(PurePosixPath(*parts).as_posix())
    return sorted(selected)


def is_pytest_file(path: str) -> bool:
    """Return whether a changed path follows this repository's pytest naming."""
    name = PurePosixPath(path).name
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


def pytest_command(paths: Iterable[str]) -> str:
    """Build a copyable pytest command for changed runnable test files."""
    command = ["python3", "-m", "pytest", "-q", *paths]
    return shlex.join(command)


def format_report(paths: Iterable[str]) -> str:
    """Format focused guidance for CI logs and the workflow summary."""
    changed_paths = select_test_paths(paths)
    runnable_paths = [path for path in changed_paths if is_pytest_file(path)]
    lines = ["## Focused test guidance (report-only)", ""]
    if not changed_paths:
        lines.append("No changed paths under `tests/`.")
    else:
        lines.extend(["Changed paths under `tests/`:", ""])
        lines.extend(f"- `{path}`" for path in changed_paths)
    lines.extend(["", "Suggested focused validation:", ""])
    if runnable_paths:
        lines.append(f"```sh\n{pytest_command(runnable_paths)}\n```")
    else:
        lines.append("No directly runnable pytest files changed.")
    lines.extend(
        [
            "",
            "This guidance does not infer tests from source changes. "
            "Existing blocking CI remains the source of truth.",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report focused pytest guidance for changed tests/ paths.",
    )
    parser.add_argument("--base-sha", help="Pull request base commit SHA.")
    parser.add_argument("--head-sha", help="Pull request head commit SHA.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if bool(args.base_sha) != bool(args.head_sha):
        raise SystemExit("--base-sha and --head-sha must be provided together")

    if args.base_sha and args.head_sha:
        paths = changed_paths_from_merge_base(args.base_sha, args.head_sha)
    else:
        paths = parse_paths(sys.stdin.buffer.read())

    print(format_report(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
