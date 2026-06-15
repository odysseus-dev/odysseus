#!/usr/bin/env python3
"""Report-only randomized test-order runner (issue #3973).

Runs pytest with the collected test items shuffled by a seeded RNG so
order-sensitive tests (hidden coupling through shared import state, module
caches, databases, etc.) surface locally. The seed is always printed, so any
failing order is reproducible with ``--seed``.

This runner is report-only: it is not wired into CI, adds no gate, and does
not change normal pytest collection or ordering. Failures it discovers should
be fixed in separate scoped PRs, not silenced here.

Examples:
    python3 tests/run_order_report.py --seed 123 -- tests/cli/ -q
    python3 tests/run_order_report.py -- tests/cli/ -q   # generates and prints a seed

The shuffle is applied through a local ``pytest_collection_modifyitems`` hook
passed to ``pytest.main`` as an in-process plugin; no conftest or global
plugin is involved. Reproduction requires the reported working directory,
seed, pytest arguments, and test environment. The exit code is pytest's own.
"""
from __future__ import annotations

import argparse
import random
import shlex
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

# Seeds are kept in the non-negative 32-bit range so they stay short enough to
# copy from a report line into a reproduction command.
SEED_MAX = 2**32 - 1


def shuffle_items(items: list, seed: int) -> None:
    """Deterministically shuffle ``items`` in place using ``seed``."""
    random.Random(seed).shuffle(items)


class OrderShuffle:
    """Local pytest plugin that shuffles collected items with a fixed seed."""

    def __init__(self, seed: int):
        self.seed = seed

    def pytest_collection_modifyitems(self, items: list) -> None:
        shuffle_items(items, self.seed)


def generate_seed() -> int:
    """Generate a fresh seed for a run that did not pass ``--seed``."""
    return random.SystemRandom().randint(0, SEED_MAX)


def seed_type(value: str) -> int:
    """argparse type: a seed in ``[0, SEED_MAX]``."""
    number = int(value)
    if not 0 <= number <= SEED_MAX:
        raise argparse.ArgumentTypeError(
            f"seed must be between 0 and {SEED_MAX}, got {value!r}"
        )
    return number


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the order-sensitivity runner."""
    parser = argparse.ArgumentParser(
        prog="run_order_report.py",
        description=(
            "Run pytest with randomized test order to surface order-sensitive "
            "tests. Report-only: prints the seed used and propagates pytest's "
            "exit code; it changes no normal pytest behavior."
        ),
        epilog=(
            "Pass pytest targets and options after a literal -- separator, "
            "e.g.: run_order_report.py --seed 123 -- tests/cli/ -q"
        ),
    )
    parser.add_argument(
        "--seed",
        type=seed_type,
        help="shuffle seed; omitted: a seed is generated and printed",
    )
    parser.add_argument(
        "pytest_args",
        nargs="*",
        metavar="-- PYTEST_ARGS",
        help="pytest targets/options forwarded after a literal --",
    )
    return parser


def runner_path() -> str:
    """Return an absolute path for copy-pasteable reproduction commands."""
    return str(Path(__file__).resolve())


def print_report_header(seed: int, pytest_args: Sequence[str]) -> None:
    """Print the seed and an exact reproduction command before running."""
    repro = [
        sys.executable,
        runner_path(),
        "--seed",
        str(seed),
        "--",
        *pytest_args,
    ]
    print(f"[order-report] working directory: {Path.cwd()}")
    print(f"[order-report] shuffling test order with seed {seed}")
    print(
        "[order-report] reproduce from this working directory with the same "
        "test environment:"
    )
    print(f"[order-report] reproduce with: {shlex.join(repro)}")


def print_report_footer(seed: int, exit_code: int) -> None:
    """Print the outcome with the seed again, after possibly long pytest output."""
    outcome = "no failures" if exit_code == 0 else f"pytest exit code {exit_code}"
    print(
        f"[order-report] seed {seed}: {outcome} "
        "(report-only; fix order-sensitive failures in separate scoped PRs)"
    )


def run(
    argv: Sequence[str] | None = None,
    pytest_main: Callable[..., int] | None = None,
) -> int:
    """Parse ``argv``, run pytest with shuffled item order, and report the seed.

    ``pytest_main`` is injected so tests can assert on the forwarded arguments
    and plugin without running a nested pytest. It must match ``pytest.main``:
    accept ``(args, plugins=...)`` and return an exit code.
    """
    namespace = build_parser().parse_args(argv)
    seed = namespace.seed if namespace.seed is not None else generate_seed()
    pytest_args = list(namespace.pytest_args)
    print_report_header(seed, pytest_args)
    if pytest_main is None:
        import pytest

        pytest_main = pytest.main
    exit_code = int(pytest_main(pytest_args, plugins=[OrderShuffle(seed)]))
    print_report_footer(seed, exit_code)
    return exit_code


def main() -> int:
    """Console entry point."""
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
