"""Regression guard for #4149: normal Resend must not delete chat history.

chat.js is browser-heavy, so this pins the source-level contract: the footer's
plain "Resend message" path appends a fresh send, while regenerate-only paths
must opt into truncating/replacing from the selected message.
"""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_CHAT_JS = _REPO / "static" / "js" / "chat.js"
_CHAT_RENDERER_JS = _REPO / "static" / "js" / "chatRenderer.js"


def _resend_body() -> str:
    src = _CHAT_JS.read_text(encoding="utf-8")
    start = src.index("export async function resendUserMessage(")
    end = src.index("export async function regenerateFrom(", start)
    return src[start:end]


def test_resend_message_does_not_truncate_by_default():
    body = _resend_body()

    assert "opts = {}" in body
    assert "const replaceFromHere = Boolean(opts && opts.replaceFromHere);" in body

    guard_idx = body.index("if (replaceFromHere)")
    truncate_idx = body.index("/api/session/${sessionId}/truncate")
    hide_idx = body.index("_hideUserBubble = true;")

    assert guard_idx < truncate_idx
    assert guard_idx < hide_idx
    assert "/truncate" not in body[:guard_idx]
    assert "_hideUserBubble = true;" not in body[:guard_idx]


def test_only_regenerate_callers_opt_into_replace_from_here():
    renderer = _CHAT_RENDERER_JS.read_text(encoding="utf-8")

    assert "window.chatModule.resendUserMessage(msgElement);" in renderer
    assert "window.chatModule.resendUserMessage(userMsgEl, { replaceFromHere: true });" in renderer
