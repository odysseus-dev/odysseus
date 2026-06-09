#!/usr/bin/env python3
"""Focused test selection runner for the pytest taxonomy markers (issue #3442).

This wraps ``pytest -m`` selection over the ``area_*`` / ``sub_*`` markers that
``tests/conftest.py`` adds at collection time (issue #3491) so focused
validation is repeatable and less error-prone than hand-written marker
expressions. It builds a pytest command line and either prints it (``--dry-run``)
or runs it.

Examples:
    tests/run_focus.py --area security
    tests/run_focus.py --area services --sub-area cookbook
    tests/run_focus.py --keyword taxonomy -- --maxfail=1 -q
    tests/run_focus.py --fast
    tests/run_focus.py --area services --fast --durations 25

This script imports no production code and changes no test behavior. It only
constructs and (optionally) executes a pytest invocation.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests._taxonomy import discover_markers, normalize_marker_name  # noqa: E402

# The canonical taxonomy areas, mirroring the ``area_*`` markers declared in
# pyproject.toml and produced by tests/_taxonomy.py.
AREAS: tuple[str, ...] = (
    "security",
    "routes",
    "services",
    "cli",
    "js",
    "helpers",
    "unit",
    "uncategorized",
)


def normalize_sub_area(value: str) -> str:
    """Normalize a CLI sub-area value and remove an optional ``sub_`` prefix."""
    token = normalize_marker_name(value)
    if token.startswith("sub_"):
        token = token.removeprefix("sub_")
    if not token:
        raise argparse.ArgumentTypeError(
            f"invalid sub-area {value!r}: must contain at least one letter or digit"
        )
    return token


def discover_sub_areas(tests_dir: Path = TESTS_DIR) -> frozenset[str]:
    """Discover valid taxonomy sub-areas from Python test filenames."""
    paths = list(tests_dir.rglob("test_*.py"))
    paths += list(tests_dir.rglob("*_test.py"))
    markers = discover_markers(paths)
    return frozenset(
        marker.removeprefix("sub_")
        for marker in markers
        if marker.startswith("sub_")
    )


def non_negative_int(value: str) -> int:
    """argparse type: a non-negative int (0 means "show all" for --durations)."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value!r}")
    return number


def non_negative_float(value: str) -> float:
    """argparse type: a non-negative float (seconds threshold for --durations-min)."""
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value!r}")
    return number


def sub_area_type(valid_sub_areas: frozenset[str]) -> Callable[[str], str]:
    """Build an argparse converter that accepts only discovered sub-areas."""

    def validate(value: str) -> str:
        sub_area = normalize_sub_area(value)
        if sub_area not in valid_sub_areas:
            raise argparse.ArgumentTypeError(
                f"unknown sub-area {value!r}; choose a discovered taxonomy sub-area"
            )
        return sub_area

    return validate


@dataclass(frozen=True)
class FocusSelection:
    """A single focused-selection request, decoupled from argparse and pytest."""

    area: str | None = None
    sub_area: str | None = None
    keyword: str | None = None
    last_failed: bool = False
    fast: bool = False
    durations: int | None = None
    durations_min: float | None = None
    pytest_args: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_focus(self) -> bool:
        """True when at least one focusing selector (not just pass-through) is set.

        Duration visibility (``durations`` / ``durations_min``) is reporting
        only, not a selector, so it does not count as focus on its own.
        """
        return bool(
            self.area
            or self.sub_area
            or self.keyword
            or self.last_failed
            or self.fast
        )


def build_marker_expression(
    area: str | None, sub_area: str | None, fast: bool = False
) -> str | None:
    """Build the ``-m`` marker expression from area, sub-area, and the fast lane.

    The fast lane adds ``not slow`` and composes with any area/sub-area with
    ``and``. Returns ``None`` when nothing is given so the caller can omit ``-m``.
    """
    parts: list[str] = []
    if area:
        parts.append(f"area_{area}")
    if sub_area:
        parts.append(f"sub_{sub_area}")
    if fast:
        parts.append("not slow")
    if not parts:
        return None
    return " and ".join(parts)


def build_pytest_command(
    selection: FocusSelection, python: str | None = None
) -> list[str]:
    """Build the pytest argv list for ``selection``.

    No shell is involved; the result is a plain argv list for subprocess. The
    interpreter defaults to the one running this script (the project venv when
    invoked as ``.venv/bin/python tests/run_focus.py``).
    """
    command = [python or sys.executable, "-m", "pytest"]
    marker_expression = build_marker_expression(
        selection.area, selection.sub_area, selection.fast
    )
    if marker_expression:
        command += ["-m", marker_expression]
    if selection.keyword:
        command += ["-k", selection.keyword]
    if selection.last_failed:
        command += ["--last-failed", "--last-failed-no-failures=none"]
    if selection.durations is not None:
        command += [f"--durations={selection.durations}"]
    if selection.durations_min is not None:
        command += [f"--durations-min={selection.durations_min}"]
    command += list(selection.pytest_args)
    return command


def selection_from_args(namespace: argparse.Namespace) -> FocusSelection:
    """Convert parsed argparse values into a ``FocusSelection``."""
    return FocusSelection(
        area=namespace.area,
        sub_area=namespace.sub_area,
        keyword=namespace.keyword,
        last_failed=namespace.last_failed,
        fast=namespace.fast,
        durations=namespace.durations,
        durations_min=namespace.durations_min,
        pytest_args=tuple(namespace.pytest_args),
    )


def build_parser(
    valid_sub_areas: frozenset[str] | None = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the focused runner."""
    if valid_sub_areas is None:
        valid_sub_areas = discover_sub_areas()
    parser = argparse.ArgumentParser(
        prog="run_focus.py",
        description=(
            "Run a focused subset of the test suite using the area_*/sub_* "
            "taxonomy markers. Combine --area and --sub-area to intersect them."
        ),
        epilog=(
            "Pass extra pytest arguments after a literal -- separator, e.g.: "
            "run_focus.py --area services -- --maxfail=1 -q"
        ),
    )
    parser.add_argument(
        "--area",
        choices=AREAS,
        help="select tests in one taxonomy area (marker area_<area>)",
    )
    parser.add_argument(
        "--sub-area",
        type=sub_area_type(valid_sub_areas),
        metavar="NAME",
        help="select tests in a sub-area (marker sub_<name>); combinable with --area",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        help="pass a keyword expression through to pytest -k",
    )
    parser.add_argument(
        "--last-failed",
        action="store_true",
        help="re-run only tests that failed on the last run (pytest --last-failed)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="fast lane: exclude tests marked slow (adds 'not slow'); composable with --area/--sub-area",
    )
    parser.add_argument(
        "--durations",
        type=non_negative_int,
        metavar="N",
        help="report the N slowest tests (pytest --durations=N, 0 shows all); not a focus selector",
    )
    parser.add_argument(
        "--durations-min",
        type=non_negative_float,
        metavar="SECONDS",
        help="minimum duration to report with --durations (pytest --durations-min)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the pytest command without executing it",
    )
    parser.add_argument(
        "pytest_args",
        nargs="*",
        metavar="-- PYTEST_ARGS",
        help="extra arguments forwarded to pytest after a literal --",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    executor: Callable[[list[str]], int] = subprocess.call,
) -> int:
    """Parse ``argv``, build the pytest command, and run or print it.

    ``executor`` is injected so tests can assert on the constructed command
    without spawning a process. It must accept an argv list and return an exit
    code, matching ``subprocess.call``.
    """
    parser = build_parser()
    namespace = parser.parse_args(argv)
    selection = selection_from_args(namespace)
    if not selection.has_focus:
        parser.error(
            "no focus selected: pass at least one of --area, --sub-area, "
            "--keyword, --last-failed, or --fast (--durations is reporting only)"
        )
    if selection.durations_min is not None and selection.durations is None:
        parser.error(
            "--durations-min has no effect without --durations; pass "
            "--durations N as well"
        )
    command = build_pytest_command(selection)
    if namespace.dry_run:
        print(shlex.join(command))
        return 0
    return executor(command)


def main() -> int:
    """Console entry point."""
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
