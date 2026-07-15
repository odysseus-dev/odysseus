import src.agent_tools  # noqa: F401
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def test_qwen_tool_call_parsing_and_stripping():
    raw = """Sure, let me check your accounts.

<|tool_call_start|>[list_email_accounts()]<|tool_call_end|>"""

    blocks = parse_tool_blocks(raw, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__list_email_accounts"
    assert blocks[0].content == "{}"
    assert strip_tool_blocks(raw, skip_fenced=True) == "Sure, let me check your accounts."


def test_qwen_tool_call_with_args():
    raw = """Okay, fetching recent messages.
<|tool_call_start|>[list_emails(account="Gmail", unread_only=True, max_results=5)]<|tool_call_end|>"""

    blocks = parse_tool_blocks(raw, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__list_emails"
    assert "Gmail" in blocks[0].content
    
    cleaned = strip_tool_blocks(raw, skip_fenced=True)
    assert cleaned == "Okay, fetching recent messages."
