"""Direct tests for the focused test-selection runner (tests/run_focus.py).

Command construction is tested separately from process execution: the pure
builder functions are asserted directly, and ``run`` is exercised with an
injected fake executor so no pytest subprocess is ever spawned.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from tests.run_focus import (
    FocusSelection,
    build_marker_expression,
    build_pytest_command,
    discover_sub_areas,
    normalize_sub_area,
    run,
)

PY = "PY"  # placeholder interpreter for deterministic command assertions


def _cmd(**kwargs) -> list[str]:
    """Build a pytest command for a FocusSelection made from kwargs."""
    return build_pytest_command(FocusSelection(**kwargs), python=PY)


# --- marker expression building -------------------------------------------


def test_area_only_marker_expression():
    assert build_marker_expression("security", None) == "area_security"


def test_sub_area_only_marker_expression():
    assert build_marker_expression(None, "cookbook") == "sub_cookbook"


def test_area_and_sub_area_marker_expression():
    assert build_marker_expression("services", "cookbook") == "area_services and sub_cookbook"


def test_no_selection_marker_expression_is_none():
    assert build_marker_expression(None, None) is None


def test_fast_only_marker_expression():
    assert build_marker_expression(None, None, fast=True) == "not slow"


def test_fast_composes_with_area():
    assert build_marker_expression("services", None, fast=True) == "area_services and not slow"


def test_fast_composes_with_area_and_sub_area():
    assert (
        build_marker_expression("services", "cookbook", fast=True)
        == "area_services and sub_cookbook and not slow"
    )


# --- command construction --------------------------------------------------


def test_area_only_command():
    assert _cmd(area="security") == [PY, "-m", "pytest", "-m", "area_security"]


def test_sub_area_only_command():
    assert _cmd(sub_area="cookbook") == [PY, "-m", "pytest", "-m", "sub_cookbook"]


def test_area_and_sub_area_command():
    assert _cmd(area="services", sub_area="cookbook") == [
        PY, "-m", "pytest", "-m", "area_services and sub_cookbook",
    ]


def test_keyword_only_command():
    assert _cmd(keyword="taxonomy") == [PY, "-m", "pytest", "-k", "taxonomy"]


def test_area_and_keyword_command():
    assert _cmd(area="services", keyword="cookbook") == [
        PY, "-m", "pytest", "-m", "area_services", "-k", "cookbook",
    ]


def test_passthrough_pytest_args_appended_last():
    command = _cmd(area="services", pytest_args=("--maxfail=1", "-q"))
    assert command == [PY, "-m", "pytest", "-m", "area_services", "--maxfail=1", "-q"]


def test_last_failed_appends_safe_flags():
    assert _cmd(last_failed=True) == [
        PY,
        "-m",
        "pytest",
        "--last-failed",
        "--last-failed-no-failures=none",
    ]


def test_default_python_is_current_interpreter():
    command = build_pytest_command(FocusSelection(area="cli"))
    assert command[0] == sys.executable


# --- fast lane and duration visibility -------------------------------------


def test_fast_only_command():
    assert _cmd(fast=True) == [PY, "-m", "pytest", "-m", "not slow"]


def test_fast_with_area_command():
    assert _cmd(area="services", fast=True) == [
        PY, "-m", "pytest", "-m", "area_services and not slow",
    ]


def test_fast_with_area_and_sub_area_command():
    assert _cmd(area="services", sub_area="cookbook", fast=True) == [
        PY, "-m", "pytest", "-m", "area_services and sub_cookbook and not slow",
    ]


def test_durations_appends_flag():
    assert _cmd(fast=True, durations=25) == [
        PY, "-m", "pytest", "-m", "not slow", "--durations=25",
    ]


def test_durations_min_appends_flag():
    assert _cmd(fast=True, durations=25, durations_min=0.05) == [
        PY, "-m", "pytest", "-m", "not slow", "--durations=25", "--durations-min=0.05",
    ]


def test_durations_is_not_a_focus_selector():
    assert FocusSelection(durations=25).has_focus is False
    assert FocusSelection(fast=True).has_focus is True


def test_durations_kept_before_passthrough_args():
    command = _cmd(fast=True, durations=25, pytest_args=("-q",))
    assert command == [PY, "-m", "pytest", "-m", "not slow", "--durations=25", "-q"]


# --- sub-area normalization ------------------------------------------------


def test_normalize_sub_area_lowercases_and_collapses():
    assert normalize_sub_area("Cook Book") == "cook_book"


def test_normalize_sub_area_strips_separators():
    assert normalize_sub_area("--owner.scope--") == "owner_scope"


def test_normalize_sub_area_removes_marker_prefix():
    assert normalize_sub_area("sub_cookbook") == "cookbook"


def test_normalize_sub_area_rejects_empty_after_normalization():
    with pytest.raises(argparse.ArgumentTypeError):
        normalize_sub_area("!!!")


def test_discover_sub_areas_from_test_filename(tmp_path):
    (tmp_path / "test_cookbook_helpers.py").write_text("", encoding="utf-8")

    assert discover_sub_areas(tmp_path) == frozenset({"cookbook"})


# --- run(): dry-run, execution, validation ---------------------------------


class _FakeExecutor:
    """Records the command it was asked to run and returns a fixed code."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> int:
        self.calls.append(command)
        return self.returncode


def test_dry_run_prints_command_and_does_not_execute(capsys):
    executor = _FakeExecutor()
    code = run(
        ["--dry-run", "--area", "services", "--sub-area", "cookbook"],
        executor=executor,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert executor.calls == []
    assert out == (
        f"{sys.executable} -m pytest "
        "-m 'area_services and sub_cookbook'\n"
    )


def test_dry_run_last_failed_prints_safe_flags(capsys):
    executor = _FakeExecutor()
    code = run(["--dry-run", "--last-failed"], executor=executor)
    out = capsys.readouterr().out
    assert code == 0
    assert executor.calls == []
    assert out == (
        f"{sys.executable} -m pytest "
        "--last-failed --last-failed-no-failures=none\n"
    )


def test_run_invokes_executor_with_built_command():
    executor = _FakeExecutor(returncode=3)
    code = run(["--keyword", "taxonomy", "--", "--maxfail=1"], executor=executor)
    assert code == 3
    assert executor.calls == [[sys.executable, "-m", "pytest", "-k", "taxonomy", "--maxfail=1"]]


def test_run_last_failed_only():
    executor = _FakeExecutor()
    run(["--last-failed"], executor=executor)
    assert executor.calls == [[
        sys.executable,
        "-m",
        "pytest",
        "--last-failed",
        "--last-failed-no-failures=none",
    ]]


@pytest.mark.parametrize("value", ["cookbook", "sub_cookbook"])
def test_run_accepts_both_sub_area_forms(value):
    executor = _FakeExecutor()
    run(["--sub-area", value], executor=executor)
    assert executor.calls == [[
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "sub_cookbook",
    ]]


def test_invalid_area_exits_with_error():
    with pytest.raises(SystemExit) as excinfo:
        run(["--area", "bogus"], executor=_FakeExecutor())
    assert excinfo.value.code == 2


def test_invalid_sub_area_exits_with_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(
            ["--sub-area", "definitely_not_a_real_sub_area"],
            executor=_FakeExecutor(),
        )
    assert excinfo.value.code == 2
    assert "unknown sub-area" in capsys.readouterr().err


def test_no_focus_selector_is_rejected():
    executor = _FakeExecutor()
    with pytest.raises(SystemExit) as excinfo:
        run(["--", "-q"], executor=executor)
    assert excinfo.value.code == 2
    assert executor.calls == []


def test_fast_run_invokes_executor_with_not_slow():
    executor = _FakeExecutor()
    run(["--fast"], executor=executor)
    assert executor.calls == [[sys.executable, "-m", "pytest", "-m", "not slow"]]


def test_fast_with_durations_run_invokes_executor():
    executor = _FakeExecutor()
    run(["--area", "services", "--fast", "--durations", "25"], executor=executor)
    assert executor.calls == [[
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "area_services and not slow",
        "--durations=25",
    ]]


def test_fast_durations_dry_run_prints_command(capsys):
    executor = _FakeExecutor()
    code = run(["--dry-run", "--fast", "--durations", "25"], executor=executor)
    out = capsys.readouterr().out
    assert code == 0
    assert executor.calls == []
    assert out == f"{sys.executable} -m pytest -m 'not slow' --durations=25\n"


def test_durations_alone_is_rejected_before_executor():
    executor = _FakeExecutor()
    with pytest.raises(SystemExit) as excinfo:
        run(["--durations", "25"], executor=executor)
    assert excinfo.value.code == 2
    assert executor.calls == []


def test_durations_zero_is_allowed_means_show_all():
    executor = _FakeExecutor()
    run(["--fast", "--durations", "0"], executor=executor)
    assert executor.calls == [[
        sys.executable, "-m", "pytest", "-m", "not slow", "--durations=0",
    ]]


@pytest.mark.parametrize("flag,value", [("--durations", "-1"), ("--durations-min", "-0.5")])
def test_negative_duration_values_are_rejected(flag, value):
    executor = _FakeExecutor()
    with pytest.raises(SystemExit) as excinfo:
        run(["--fast", flag, value], executor=executor)
    assert excinfo.value.code == 2
    assert executor.calls == []


@pytest.mark.parametrize("argv", [
    ["--fast", "--durations-min", "0.05"],
    ["--area", "services", "--durations-min", "0.05"],
])
def test_durations_min_without_durations_is_rejected(argv):
    executor = _FakeExecutor()
    with pytest.raises(SystemExit) as excinfo:
        run(argv, executor=executor)
    assert excinfo.value.code == 2
    assert executor.calls == []


def test_durations_min_with_durations_is_allowed():
    executor = _FakeExecutor()
    run(["--fast", "--durations", "25", "--durations-min", "0.05"], executor=executor)
    assert executor.calls == [[
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not slow",
        "--durations=25",
        "--durations-min=0.05",
    ]]


# --- fast lane deselects evidence-backed slow tests (real collection) -------

# Node names in tests/test_auth_config_lock_concurrency.py: the single unmarked
# fast test, and the five @pytest.mark.slow tests the fast lane must exclude.
_FAST_AUTH_CONCURRENCY_TEST = "test_parallel_creates_same_username_only_one_wins"
_SLOW_AUTH_CONCURRENCY_TESTS = (
    "test_parallel_creates_no_lost_users",
    "test_parallel_deletes_no_corruption",
    "test_parallel_renames_no_lost_users",
    "test_mixed_operations_no_corruption",
    "test_file_always_valid_json_during_concurrent_ops",
)


def test_fast_lane_collects_only_unmarked_auth_concurrency_test():
    """`--fast` collection drops the marked slow tests but keeps the fast one.

    Unlike the other tests here, this runs a real `--collect-only` so it proves
    the `slow` markers actually deselect during collection, not just that the
    command is built with `not slow`.
    """
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "tests/run_focus.py",
            "--fast",
            "--",
            "--collect-only",
            "-q",
            "tests/test_auth_config_lock_concurrency.py",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    collected = result.stdout

    assert _FAST_AUTH_CONCURRENCY_TEST in collected
    for slow_test in _SLOW_AUTH_CONCURRENCY_TESTS:
        assert slow_test not in collected, f"slow test was not deselected: {slow_test}"
