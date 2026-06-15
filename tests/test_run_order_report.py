"""Direct tests for the order-sensitivity report runner (tests/run_order_report.py).

The shuffle and argument plumbing are tested without spawning pytest: the
shuffle helpers are asserted directly and ``run`` is exercised with an
injected fake ``pytest.main``. A small subprocess test then proves the seed is
applied end to end (reproducible, seed visible) against a throwaway test file,
never the real suite.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.run_order_report import (
    SEED_MAX,
    OrderShuffle,
    generate_seed,
    run,
    shuffle_items,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "tests" / "run_order_report.py"


class _FakePytestMain:
    """Records forwarded args and plugins and returns a fixed exit code."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[tuple[list[str], list]] = []

    def __call__(self, args: list[str], plugins: list) -> int:
        self.calls.append((list(args), list(plugins)))
        return self.returncode


# --- shuffle determinism -----------------------------------------------------


def test_same_seed_shuffles_identically():
    first = list(range(20))
    second = list(range(20))
    shuffle_items(first, seed=123)
    shuffle_items(second, seed=123)
    assert first == second


def test_different_seeds_shuffle_differently():
    first = list(range(20))
    second = list(range(20))
    shuffle_items(first, seed=123)
    shuffle_items(second, seed=321)
    assert first != second


def test_shuffle_preserves_items():
    items = list(range(20))
    shuffle_items(items, seed=123)
    assert sorted(items) == list(range(20))


def test_plugin_hook_matches_shuffle_items():
    hooked = list(range(20))
    expected = list(range(20))
    OrderShuffle(seed=7).pytest_collection_modifyitems(hooked)
    shuffle_items(expected, seed=7)
    assert hooked == expected


# --- argument parsing and pytest invocation ----------------------------------


def test_pytest_args_after_separator_are_forwarded():
    fake = _FakePytestMain()
    run(["--seed", "123", "--", "tests/cli/", "-q"], pytest_main=fake)
    (args, plugins), = fake.calls
    assert args == ["tests/cli/", "-q"]
    assert [type(p) for p in plugins] == [OrderShuffle]


def test_explicit_seed_reaches_plugin():
    fake = _FakePytestMain()
    run(["--seed", "123", "--", "-q"], pytest_main=fake)
    (_, plugins), = fake.calls
    assert plugins[0].seed == 123


def test_pytest_exit_code_is_propagated():
    fake = _FakePytestMain(returncode=3)
    assert run(["--seed", "123", "--", "-q"], pytest_main=fake) == 3


@pytest.mark.parametrize("value", ["abc", "-1", str(SEED_MAX + 1)])
def test_invalid_seed_is_rejected_before_pytest(value):
    fake = _FakePytestMain()
    with pytest.raises(SystemExit) as excinfo:
        run(["--seed", value, "--", "-q"], pytest_main=fake)
    assert excinfo.value.code == 2
    assert fake.calls == []


# --- seed reporting -----------------------------------------------------------


def test_explicit_seed_is_printed_with_repro_command(capsys):
    run(["--seed", "123", "--", "tests/cli/", "-q"], pytest_main=_FakePytestMain())
    out = capsys.readouterr().out
    assert "[order-report] shuffling test order with seed 123" in out
    repro = shlex.join(
        [
            sys.executable,
            str(RUNNER),
            "--seed",
            "123",
            "--",
            "tests/cli/",
            "-q",
        ]
    )
    assert f"reproduce with: {repro}" in out


def test_working_directory_is_reported(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    run(["--seed", "123", "--", "-q"], pytest_main=_FakePytestMain())
    out = capsys.readouterr().out
    assert f"[order-report] working directory: {tmp_path}" in out


def test_footer_repeats_seed_and_outcome(capsys):
    run(["--seed", "123", "--", "-q"], pytest_main=_FakePytestMain(returncode=1))
    out = capsys.readouterr().out
    assert "[order-report] seed 123: pytest exit code 1" in out


def test_generated_seed_is_printed_and_used(capsys):
    fake = _FakePytestMain()
    run(["--", "-q"], pytest_main=fake)
    out = capsys.readouterr().out
    seed_line = next(line for line in out.splitlines() if "with seed" in line)
    seed = int(seed_line.rsplit("seed ", 1)[1])
    assert 0 <= seed <= SEED_MAX
    (_, plugins), = fake.calls
    assert plugins[0].seed == seed


def test_generate_seed_is_within_range():
    assert all(0 <= generate_seed() <= SEED_MAX for _ in range(5))


# --- end-to-end: the seed really drives collection order (real subprocess) ---

_SAMPLE_TESTS = "".join(
    f"def test_{name}():\n    pass\n\n"
    for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")
)


@pytest.fixture(scope="module")
def sample_suite(tmp_path_factory) -> Path:
    """A throwaway directory with eight trivial tests, outside the repo rootdir."""
    suite = tmp_path_factory.mktemp("order_report_suite")
    (suite / "test_sample.py").write_text(_SAMPLE_TESTS, encoding="utf-8")
    return suite


def _collect_order(sample_suite: Path, seed: int) -> tuple[list[str], str]:
    """Run the runner with ``--collect-only`` and return (test ids, stdout)."""
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--seed",
            str(seed),
            "--",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "test_sample.py",
        ],
        cwd=sample_suite,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    ids = [line for line in result.stdout.splitlines() if "::" in line]
    assert len(ids) == 8, result.stdout
    return ids, result.stdout


def test_subprocess_same_seed_is_reproducible(sample_suite):
    first, out = _collect_order(sample_suite, seed=123)
    second, _ = _collect_order(sample_suite, seed=123)
    assert first == second
    assert "[order-report] shuffling test order with seed 123" in out


def test_subprocess_different_seeds_change_order(sample_suite):
    first, _ = _collect_order(sample_suite, seed=123)
    second, _ = _collect_order(sample_suite, seed=321)
    assert first != second


def test_subprocess_failure_exit_code_and_footer(tmp_path):
    """A real failing pytest run keeps pytest's exit code and reports the seed."""
    (tmp_path / "test_failure.py").write_text(
        "def test_failure():\n    assert False\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--seed",
            "123",
            "--",
            "test_failure.py",
            "-q",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    repro = shlex.join(
        [
            sys.executable,
            str(RUNNER),
            "--seed",
            "123",
            "--",
            "test_failure.py",
            "-q",
        ]
    )
    assert f"reproduce with: {repro}" in result.stdout
    assert "[order-report] seed 123: pytest exit code 1" in result.stdout
