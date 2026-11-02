"""Regression guard for SFT-safe resend behavior.

chat.js is browser-heavy, so this pins the source-level contract: the footer's
plain "Resend message" path replaces the failed user+assistant attempt instead
of appending a duplicate user turn. Append-only resend must be explicit.
"""

from pathlib import Path
from types import SimpleNamespace

from routes.history.history_routes import _keep_count_before_message
from tests.helpers.stylesheets import app_css


_REPO = Path(__file__).resolve().parent.parent
_CHAT_JS = _REPO / "static" / "js" / "chat.js"
_CHAT_RENDERER_JS = _REPO / "static" / "js" / "chatRenderer.js"


def _resend_body() -> str:
    src = _CHAT_JS.read_text(encoding="utf-8")
    start = src.index("export async function resendUserMessage(")
    end = src.index("export async function regenerateFrom(", start)
    return src[start:end]


def _regenerate_body() -> str:
    src = _CHAT_JS.read_text(encoding="utf-8")
    start = src.index("export async function regenerateFrom(")
    end = src.index("export async function deleteMessage(", start)
    return src[start:end]


def test_resend_message_replaces_by_default():
    body = _resend_body()

    assert "opts = {}" in body
    assert "const appendOnly = Boolean(opts && opts.append);" in body
    assert "const replaceFromHere = !appendOnly || Boolean(opts && opts.replaceFromHere);" in body

    guard_idx = body.index("if (replaceFromHere)")
    truncate_idx = body.index("/api/session/${sessionId}/truncate")

    assert guard_idx < truncate_idx
    assert "appendOnly" in body[:guard_idx]
    assert "_hideUserBubble = true;" not in body
    assert "const beforeMsgId = userMsgElement.dataset.dbId || '';" in body
    assert "before_msg_id: beforeMsgId" in body
    assert "keep_count: keepCount" in body


def test_resend_removes_rendered_tool_threads_between_turns():
    body = _resend_body()

    assert "including agent-thread tool history that is not a `.msg` bubble" in body
    assert "let node = userMsgElement;" in body
    assert "node = node.nextElementSibling;" in body
    assert "prev.remove();" in body
    assert "querySelectorAll('.msg')" not in body[body.index("if (replaceFromHere)"):]


def test_regenerate_clears_tool_threads_after_user_bubble():
    body = _regenerate_body()

    assert "agent-thread tool history between the user and AI bubble" in body
    assert "let node = userMsgEl.nextElementSibling;" in body
    assert "node = node.nextElementSibling;" in body
    assert "prev.remove();" in body


def test_footer_resend_uses_default_replacement_behavior():
    renderer = _CHAT_RENDERER_JS.read_text(encoding="utf-8")

    assert "window.chatModule.resendUserMessage(msgElement);" in renderer
    assert "window.chatModule.resendUserMessage(userMsgEl, { replaceFromHere: true });" in renderer


def test_footer_resend_uses_round_svg_icon_not_text_glyph():
    renderer = _CHAT_RENDERER_JS.read_text(encoding="utf-8")
    style = app_css()

    assert "const RESEND_ICON =" in renderer
    assert "resend-message-icon" in renderer
    assert "icon: RESEND_ICON" in renderer
    assert "html: true" in renderer
    assert "icon: '\\u21BB'" not in renderer
    assert ".msg-action-btn .resend-message-icon" in style
    assert ".overflow-icon .resend-message-icon" in style


def test_truncate_can_resolve_keep_count_from_db_message_id():
    rows = [
        SimpleNamespace(id="first"),
        SimpleNamespace(id="clicked-user"),
        SimpleNamespace(id="assistant-after"),
    ]

    assert _keep_count_before_message(rows, "clicked-user") == 1
    assert _keep_count_before_message(rows, "missing") is None
    assert _keep_count_before_message(rows, "") is None
