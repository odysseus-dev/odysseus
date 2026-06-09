"""Direct tests for the focused test-selection runner (tests/run_focus.py).

Command construction is tested separately from process execution: the pure
builder functions are asserted directly, and ``run`` is exercised with an
injected fake executor so no pytest subprocess is ever spawned.
"""
from __future__ import annotations

import argparse
import sys

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
