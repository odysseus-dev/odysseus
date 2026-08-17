"""Regression guards for retaining chat-composer focus after submit."""

from pathlib import Path


_CHAT_PATH = Path(__file__).resolve().parent.parent / "static" / "js" / "chat.js"


def _function_body(source: str, function_name: str) -> str:
    start = source.index(f"function {function_name}(")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced function {function_name}")


def test_focus_restore_avoids_moving_the_transcript():
    source = _CHAT_PATH.read_text(encoding="utf-8")
    helper = _function_body(source, "_restoreComposerFocus")
    assert "messageInput.disabled" in helper
    assert "window.innerWidth <= 768" in helper
    assert "messageInput.focus({ preventScroll: true });" in helper
    assert "messageInput.focus();" in helper


def test_normal_send_refocuses_after_clearing_the_composer():
    source = _CHAT_PATH.read_text(encoding="utf-8")
    clear = "messageInput.dispatchEvent(new Event('input'));"
    restore = "_restoreComposerFocus(messageInput);"
    start = source.index("const userDisplay = _displayOverride || msg;")
    upload = source.index("let ids = [];", start)
    send_clear_path = source[start:upload]
    assert clear in send_clear_path
    assert restore in send_clear_path
    assert send_clear_path.index(clear) < send_clear_path.index(restore)


def test_send_path_preserves_mobile_keyboard_dismissal():
    source = _CHAT_PATH.read_text(encoding="utf-8")
    start = source.index("const userDisplay = _displayOverride || msg;")
    upload = source.index("let ids = [];", start)
    send_clear_path = source[start:upload]
    assert "if (window.innerWidth <= 768)" in send_clear_path
    assert "messageInput.blur()" in send_clear_path
    assert "setAttribute('readonly'" in send_clear_path


def test_stream_completion_does_not_override_the_users_focus_choice():
    source = _CHAT_PATH.read_text(encoding="utf-8")
    start = source.index("// Re-enable without stealing desktop focus")
    end = source.index("// Clear tracking variables", start)
    completion = source[start:end]
    assert "messageInput.disabled = false;" in completion
    assert "messageInput.focus" not in completion
    assert "if (window.innerWidth <= 768) messageInput.blur();" in completion
