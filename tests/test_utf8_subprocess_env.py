"""Regression for the Windows Cookbook charmap crash (issue #1543).

Cookbook downloads / dependency installs spawn a detached child whose hf/pip
grandchildren print non-ASCII (e.g. hf's "Token is valid (✓)", U+2713). On
Windows the child's stdio defaults to cp1252 and dies with a UnicodeEncodeError
('charmap' codec can't encode '\\u2713'), masked as exit 0. `utf8_subprocess_env`
forces UTF-8 stdio so the child survives; it's a no-op on POSIX.
"""

import os

from core.platform_compat import utf8_subprocess_env


def test_sets_utf8_io_vars_and_preserves_base():
    env = utf8_subprocess_env({"FOO": "bar"})
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["FOO"] == "bar"  # caller's other vars survive


def test_overrides_conflicting_locale_values():
    env = utf8_subprocess_env({"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_does_not_mutate_the_passed_base():
    base = {"X": "1"}
    utf8_subprocess_env(base)
    assert "PYTHONUTF8" not in base
    assert "PYTHONIOENCODING" not in base


def test_defaults_to_a_copy_of_os_environ(monkeypatch):
    monkeypatch.setenv("ODY_TEST_MARKER", "present")
    env = utf8_subprocess_env()
    assert env.get("ODY_TEST_MARKER") == "present"  # inherits real environment
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    # it's a copy — mutating the result never touches os.environ
    env["ODY_SHOULD_NOT_LEAK"] = "x"
    assert "ODY_SHOULD_NOT_LEAK" not in os.environ
