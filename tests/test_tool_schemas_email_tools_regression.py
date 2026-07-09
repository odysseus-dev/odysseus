"""Regression: native function-call conversion must know built-in email tools."""

from src.tool_schemas import function_call_to_tool_block


def test_native_email_function_call_maps_to_builtin_email_mcp_tool():
    block = function_call_to_tool_block("list_emails", "{}")

    assert block is not None
    assert block.tool_type == "mcp__email__list_emails"
    assert block.content == "{}"
