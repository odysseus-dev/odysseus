from pathlib import Path


def test_tool_approval_bypasses_polymorphic_send_button_actions():
    root = Path(__file__).resolve().parents[1]
    chat = (root / "static/js/chat.js").read_text(encoding="utf-8")
    stream = (root / "static/js/chatStream.js").read_text(encoding="utf-8")

    # chat.js still defers the sealed approval through a synthetic button click.
    assert "if (sendButton) sendButton.click();" in chat

    # The capture listener must intercept only that synthetic click and route it
    # through the chat form submit path, before app.js can reinterpret an empty
    # composer as New chat or Record voice.
    assert "if (event.isTrusted) return;" in stream
    assert "event.stopImmediatePropagation();" in stream
    assert "chatForm.requestSubmit()" in stream
    assert "sendButton.dataset.mode = ''" not in stream


def test_ask_user_close_button_uses_one_css_glyph():
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    styles = (root / "static/style.css").read_text(encoding="utf-8")

    assert "closeBtn.className = 'modal-close ask-user-close';" in renderer
    assert "closeBtn.setAttribute('aria-label', 'Dismiss question');" in renderer
    assert "closeBtn.textContent = '×';" not in renderer
    assert ".modal-close::before" in styles
