"""Failure diagnostics must survive alongside model-visible tool output."""

import pytest

from src.tool_execution import format_tool_result


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"stdout": "partial output", "stderr": "", "exit_code": 124}, "partial output"),
        ({"stdout": "", "stderr": "", "exit_code": 124}, "124"),
        ({"output": "partial output", "exit_code": 124}, "partial output"),
        ({"content": "partial document", "size": 16}, "partial document"),
        ({"response": "provider summary"}, "provider summary"),
        ({"results": "partial search results"}, "partial search results"),
    ],
    ids=["partial-stdout", "empty-stdout", "output", "content", "response", "results"],
)
def test_error_is_preserved_alongside_result_body(body, expected):
    rendered = format_tool_result("operation", {**body, "error": "Recovery diagnostic"})

    assert expected in rendered
    assert rendered.count("Recovery diagnostic") == 1
    assert "**Error:** Recovery diagnostic" in rendered


@pytest.mark.parametrize("body", [{}, {"success": False}, {"exit_code": 1}])
def test_error_without_output_is_rendered_once(body):
    rendered = format_tool_result("operation", {**body, "error": "Operation failed"})

    assert rendered.count("Operation failed") == 1
    assert "Error: unknown" not in rendered


@pytest.mark.parametrize("error", [None, ""])
def test_success_with_empty_error_has_no_failure_label(error):
    rendered = format_tool_result("operation", {"output": "done", "exit_code": 0, "error": error})

    assert "done" in rendered
    assert "Error" not in rendered


def test_unspecified_failure_keeps_fallback_diagnostic():
    rendered = format_tool_result("operation", {"success": False})

    assert "Error: unknown" in rendered


@pytest.mark.asyncio
async def test_python_timeout_preserves_real_partial_output_and_diagnostic(monkeypatch, tmp_path):
    from src.agent_tools import subprocess_tools

    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(subprocess_tools, "DEFAULT_PYTHON_TIMEOUT", 1)
    result = await subprocess_tools.PythonTool().execute(
        "import time; print('partial output', flush=True); time.sleep(30)",
        {},
    )
    rendered = format_tool_result("python", result)

    assert result["exit_code"] == 124
    assert "partial output" in rendered
    assert "124" in rendered
    assert rendered.count(result["error"]) == 1
