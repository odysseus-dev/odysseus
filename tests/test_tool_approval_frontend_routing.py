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


def test_ask_user_number_shortcuts_reuse_option_click_path():
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    start = renderer.index("function _handleAskUserShortcut(event)")
    end = renderer.index("document.addEventListener('keydown', _handleAskUserShortcut);", start)
    shortcut = renderer[start:end]

    assert "if (!/^[1-3]$/.test(event.key)) return;" in shortcut
    assert "event.repeat" in shortcut
    assert "event.ctrlKey" in shortcut
    assert "event.altKey" in shortcut
    assert "event.metaKey" in shortcut
    assert "event.shiftKey" in shortcut
    assert "input, textarea, select, [contenteditable=\"true\"]" in shortcut
    assert "card.querySelectorAll('.ask-user-option')[Number(event.key) - 1]" in shortcut
    assert "event.preventDefault();" in shortcut
    assert "option.click();" in shortcut


def test_digit_shortcuts_never_answer_a_tool_approval_card():
    """A stray digit must not grant a scope the user did not deliberately pick."""

    root = Path(__file__).resolve().parents[1]
    renderer = (root / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    start = renderer.index("function _handleAskUserShortcut(event)")
    end = renderer.index("document.addEventListener('keydown', _handleAskUserShortcut);", start)
    shortcut = renderer[start:end]

    assert "if (card.dataset.askUserKind === 'tool_approval') return;" in shortcut
    # The renderer has to label the card for that guard to ever fire.
    assert (
        "card.dataset.askUserKind = isToolApproval ? 'tool_approval' : 'question';"
        in renderer
    )


def test_ask_user_renderer_accepts_scoped_root_and_submit_callback():
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "static/js/chatRenderer.js").read_text(encoding="utf-8")

    assert "const chatBox = renderOptions.root || document.getElementById('chat-history');" in renderer
    assert "const onSubmit = typeof renderOptions.onSubmit === 'function'" in renderer
    assert "kind: 'answer'" in renderer
    assert "kind: 'tool_approval'" in renderer
    assert "if (accepted !== false) card.remove();" in renderer
    assert "document.dispatchEvent(new CustomEvent('odysseus:tool-approval', { detail }))" in renderer


def test_every_changed_approval_module_is_cache_busted_together():
    """A stale module here silently reinterprets the approval click.

    chat.js leaves the composer empty and clicks the polymorphic send button,
    so a browser that pairs the new chat.js with a cached chatStream.js has no
    interceptor and lands on the New chat branch instead. The same holds for
    the compare pane modules, which chatRenderer now shares a keydown listener
    with.
    """

    root = Path(__file__).resolve().parents[1]
    version = "20260819approvalcontrol1"
    index = (root / "static/index.html").read_text(encoding="utf-8")
    app = (root / "static/app.js").read_text(encoding="utf-8")
    chat = (root / "static/js/chat.js").read_text(encoding="utf-8")
    compare_index = (root / "static/js/compare/index.js").read_text(encoding="utf-8")
    compare_stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert f"chatStream.js?v={version}" in index
    assert f"chatStream.js?v={version}" in chat
    assert f"compare/index.js?v={version}" in app
    assert f"stream.js?v={version}" in compare_index
    # One chatRenderer instance, so the ask_user keydown listener binds once.
    assert f"chatRenderer.js?v={version}" in compare_stream
