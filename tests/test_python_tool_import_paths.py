import os

from src.agent_tools.subprocess_tools import _python_with_configured_import_paths


def test_python_tool_import_paths_are_opt_in():
    source = "print('ok')"
    assert _python_with_configured_import_paths(source, {}) == source


def test_python_tool_import_paths_include_only_absolute_configured_roots():
    # os.pathsep is ':' on POSIX and ';' on Windows, and a drive-less path such
    # as '/vetted/one' is not absolute on Windows, so build both per platform.
    one = os.path.abspath(os.sep + os.path.join("vetted", "one"))
    two = os.path.abspath(os.sep + os.path.join("vetted", "two"))
    wrapped = _python_with_configured_import_paths(
        "print('ok')",
        {"ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES": os.pathsep.join([one, "relative", two])},
    )
    assert repr(one) in wrapped
    assert repr(two) in wrapped
    assert "relative" not in wrapped
    assert "exec(compile(" in wrapped
