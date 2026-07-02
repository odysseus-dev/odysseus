from pathlib import Path


def test_active_email_reader_blocks_immediate_reply_tools():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    guard_start = source.index("if active_email_ctx and active_email_ctx.get(\"uid\"):")
    guard_block = source[guard_start:source.index("# Enforce per-user privileges", guard_start)]

    assert '"reply_to_email"' in guard_block
    assert '"mcp__email__reply_to_email"' in guard_block
    assert '"send_email"' in guard_block
    assert '"mcp__email__send_email"' in guard_block
    assert '"create_document"' in guard_block
