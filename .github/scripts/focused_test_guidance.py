#!/usr/bin/env python3
"""Report focused pytest guidance for changed paths under tests/."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Iterable
from pathlib import PurePosixPath


def parse_paths(raw_paths: bytes) -> list[str]:
    """Decode the NUL-delimited output of ``git diff --name-only -z``."""
    return [os.fsdecode(path) for path in raw_paths.split(b"\0") if path]


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


def main() -> int:
    print(format_report(parse_paths(sys.stdin.buffer.read())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
