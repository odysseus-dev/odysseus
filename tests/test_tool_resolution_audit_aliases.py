from src.agent_loop import _native_tool_name_was_accepted


def test_native_resolution_audit_accepts_namespaced_equivalent():
    accepted = {"mcp__email__list_emails"}

    assert _native_tool_name_was_accepted("list_emails", accepted)
    assert _native_tool_name_was_accepted("mcp__email__list_emails", accepted)
    assert not _native_tool_name_was_accepted("read_email", accepted)


def test_native_resolution_audit_accepts_bare_equivalent():
    assert _native_tool_name_was_accepted(
        "mcp__email__send_email", {"send_email"}
    )
