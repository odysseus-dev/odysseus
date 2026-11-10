import os
import sys

import pytest

from launcher import parse_interpreter_args
from src.mcp_manager import McpManager


@pytest.mark.parametrize("argv, kind, target, child_argv", [
    (["-c", "print(1)", "a", "b"], "code", "print(1)", ["-c", "a", "b"]),
    (["-"], "stdin", None, ["-"]),
    (["-", "x"], "stdin", None, ["-", "x"]),
    (["-m", "pip", "--version"], "module", "pip", ["pip", "--version"]),
    ([r"C:\app\tool.py", "one", "two"], "script", r"C:\app\tool.py", [r"C:\app\tool.py", "one", "two"]),
])
def test_recognised_forms(argv, kind, target, child_argv):
    spec = parse_interpreter_args(argv)
    assert (spec["kind"], spec["target"], spec["argv"]) == (kind, target, child_argv)
    assert spec["flags"] == set()


def test_leading_flags_and_two_token_flags():
    spec = parse_interpreter_args(["-I", "-u", "-X", "utf8", "-W", "ignore", "-B", "-c", "pass"])
    assert spec["kind"] == "code" and spec["flags"] == {"-I", "-u", "-B"}
    assert spec["argv"] == ["-c"]


def test_flags_after_the_program_belong_to_the_program():
    spec = parse_interpreter_args(["-c", "pass", "-I"])
    assert spec["flags"] == set() and spec["argv"] == ["-c", "-I"]


@pytest.mark.parametrize("argv", [
    [],
    ["-I"],
    ["-c"],
    ["-m"],
    ["-X"],
    ["--multiprocessing-fork"],
    ["notes.txt"],
    ["-z", "-c", "pass"],
])
def test_other_arguments_start_the_app(argv):
    assert parse_interpreter_args(argv) is None


class _Stderr:
    def fileno(self):
        return 2


class _NoFileno:
    pass


def test_errlog_passes_working_stderr_through(monkeypatch):
    err = _Stderr()
    monkeypatch.setattr(sys, "stderr", err)
    assert McpManager._stdio_errlog() is err


def test_errlog_uses_real_file_without_fileno(monkeypatch, tmp_path):
    import src.mcp_manager as mm
    from src import constants
    monkeypatch.setattr(sys, "stderr", _NoFileno())
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mm, "_errlog_file", None)
    log = McpManager._stdio_errlog()
    try:
        assert isinstance(log.fileno(), int)
        assert os.path.dirname(log.name) == str(tmp_path / "logs")
        assert McpManager._stdio_errlog() is log
    finally:
        log.close()
