"""The "Vision: <model>" label on a chat photo appeared only after a reload.

The live `attachments` SSE handler found the user's bubble with
`#chat-history .msg-user:last-of-type`. :last-of-type matches the last element
of its TYPE among siblings, and by the time the event arrives the streaming
reply <div class="msg msg-ai streaming"> is the last div in #chat-history, so
the selector matched nothing and the label was never added live. The
thumbnail still appeared because updateMessageAttachments draws it separately.
"""
from pathlib import Path

CHAT = (Path(__file__).resolve().parents[1] / "static" / "js" / "chat.js").read_text(encoding="utf-8")


def _handler():
    start = CHAT.index("} else if (json.type === 'attachments') {")
    return CHAT[start:CHAT.index("attach-vision-model", start)]


def test_attachments_handler_takes_the_last_user_bubble_not_last_of_type():
    handler = _handler()
    assert "querySelector('#chat-history .msg-user:last-of-type')" not in handler
    assert "document.querySelectorAll('#chat-history .msg-user')" in handler
    assert "_ubs[_ubs.length - 1]" in handler


def test_the_streaming_reply_is_a_div_sibling_which_is_why_last_of_type_failed():
    # The premise of the fix: the reply holder is a <div> in the same list.
    assert "holder = document.createElement('div');\n      holder.className = 'msg msg-ai streaming';" in CHAT.replace("\r\n", "\n")
