from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "static/js/chat.js").read_text()


def test_queued_stack_is_consumed_as_one_ordered_message():
    assert "function _consumeQueuedRequestStack()" in CHAT
    assert "_queuedAgentRequests[i].sessionId === sid" in CHAT
    assert ".join('\\n\\n')" in CHAT


def test_automatic_drain_sends_the_combined_stack_once():
    drain = CHAT[CHAT.index("function _drainQueuedAgentRequests()") : CHAT.index("/**\n   * Handle chat form submission")]
    assert "const next = _consumeQueuedRequestStack();" in drain
    assert "_removeQueuedRequest(next.id)" not in drain
    assert "_setComposerAndSend(next.message);" in drain


def test_clicking_a_queued_bubble_promotes_the_whole_stack():
    promote = CHAT[CHAT.index("function _promoteQueuedRequest(id)") : CHAT.index("function _queueAgentRequest(message)")]
    assert "const item = _consumeQueuedRequestStack();" in promote
