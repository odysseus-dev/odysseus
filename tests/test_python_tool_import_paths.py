from src.agent_tools.subprocess_tools import _python_with_configured_import_paths


def test_python_tool_import_paths_are_opt_in():
    source = "print('ok')"
    assert _python_with_configured_import_paths(source, {}) == source


def test_python_tool_import_paths_include_only_absolute_configured_roots():
    wrapped = _python_with_configured_import_paths(
        "print('ok')",
        {"ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES": "/vetted/one:relative:/vetted/two"},
    )
    assert "'/vetted/one'" in wrapped
    assert "'/vetted/two'" in wrapped
    assert "relative" not in wrapped
    assert "exec(compile(" in wrapped
