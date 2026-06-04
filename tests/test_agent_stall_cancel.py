"""Agent-mode stall detection (issue #280).

When the model endpoint is reachable but an agent run produces no token —
typically the model choking on the tool-definition prompt (context too small,
common with LM Studio defaults, or no tool-calling support) — older builds left
the user on the full agent timeout with no explanation. The endpoint probe's
"alive" branch must instead, *in agent mode only*, flag the likely fix and
auto-cancel after a generous grace window with an actionable error.

These pin the chat.js wiring in source (mirrors test_chat_stream_scope.py).
"""
from pathlib import Path

SRC = Path("static/js/chat.js").read_text(encoding="utf-8")


def test_agent_stall_watchdog_is_agent_gated_and_cancels():
    assert "const AGENT_STALL_GRACE_S = 50;" in SRC
    alive_idx = SRC.index("} else if (status.alive) {")
    branch = SRC[alive_idx:alive_idx + 2000]
    # Only agent mode arms the watchdog...
    assert "if (_isAgent) {" in branch
    assert "agentStallTimer = setInterval(" in branch
    assert "currentAbort._reason = 'agent-stall';" in branch
    assert "currentAbort.abort();" in branch
    # ...plain chat keeps the old, non-cancelling wait message.
    assert "waiting for first token" in branch


def test_agent_stall_timer_is_declared_and_cleared():
    # Declared in the outer scope and torn down in clearProcessingProbe so the
    # interval never outlives the request (e.g. once the first token arrives).
    assert "let agentStallTimer = null;" in SRC
    clear_idx = SRC.index("const clearProcessingProbe = () => {")
    clear_body = SRC[clear_idx:clear_idx + 400]
    assert "clearInterval(agentStallTimer)" in clear_body
    assert "agentStallTimer = null;" in clear_body


def test_agent_stall_error_message_is_actionable():
    assert "if (abortReason === 'agent-stall') {" in SRC
    msg_idx = SRC.index("if (abortReason === 'agent-stall') {")
    msg_body = SRC[msg_idx:msg_idx + 800]
    assert "context" in msg_body.lower()
    assert "tool calling" in msg_body.lower()
