from src.tool_execution import _normalize_mcp_text_error


def test_explicit_text_error_is_failure_for_qualified_and_legacy_mcp_paths():
    result = _normalize_mcp_text_error({
        "stdout": "Error: Search failed: offline", "stderr": "", "exit_code": 0,
    })

    assert result["exit_code"] == 1
    assert result["error"] == "Search failed: offline"


def test_normal_mcp_text_result_remains_successful():
    result = {"stdout": "Found 2 emails", "stderr": "", "exit_code": 0}

    assert _normalize_mcp_text_error(result) == result
