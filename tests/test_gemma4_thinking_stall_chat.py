"""Source-level checks for universal thinking-only stall wiring in chat.js."""

from pathlib import Path


def test_chat_js_imports_thinking_stall_policy():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "from './model/modelStreamQuirks.js'" in source
    assert "resolveThinkingStallPolicy" in source
    assert "MIN_REPLY_AFTER_THINKING_CHARS" in source
    assert "THINKING_ONLY_TIMEOUT_MS" in source


def test_chat_js_universal_thinking_watchdog():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "_thinkingClosedAt" in source
    assert "_handleThinkingStallTimeout" in source
    assert "_renderThinkingStallError" in source
    assert "_tryThinkingOnlyNudge" in source
    assert "thinking_stall" in source


def test_chat_js_in_chat_error_on_timeout():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "thinking-stall-error" in source
    assert "Model stalled after reasoning" in source
