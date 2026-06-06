"""Source-level checks for gemma4 thinking-only stall wiring in chat.js."""

from pathlib import Path


def test_chat_js_imports_model_stream_quirks():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "from './model/modelStreamQuirks.js'" in source
    assert "getModelStreamQuirk" in source
    assert "MIN_REPLY_AFTER_THINKING_CHARS" in source


def test_chat_js_thinking_only_watchdog():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "_thinkingClosedAt" in source
    assert "_handleThinkingOnlyStall" in source
    assert "_tryThinkingOnlyNudge" in source
    assert "thinkingOnlyStallMs" in source
