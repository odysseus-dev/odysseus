from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


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


def test_ask_user_card_has_no_close_button_and_chat_scale_text():
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    styles = app_css()

    assert "closeBtn.className = 'modal-close ask-user-close';" not in renderer
    assert "closeBtn.setAttribute('aria-label', 'Dismiss question');" not in renderer
    assert "card.appendChild(head);" not in renderer
    assert "otherSend.textContent" not in renderer
    assert "otherSend.innerHTML" in renderer
    assert 'd="M12 19V5M5 12l7-7 7 7"' in renderer
    assert "ask-user-card-attached" in renderer
    assert "has-ask-user-bottom" in renderer
    assert ".ask-user-head" in styles
    assert ".ask-user-close" in styles
    assert "display: none !important;" in styles
    question_block = styles[styles.index(".ask-user-question {"):styles.index(".ask-user-options {")]
    card_block = styles[styles.index(".ask-user-card {"):styles.index(".ask-user-card-attached {")]
    attached_block = styles[styles.index(".ask-user-card-attached {"):styles.index(".ask-user-card-attached::before {")]
    connector_block = styles[styles.index(".ask-user-card-attached::before {"):styles.index("/* Focused only programmatically", styles.index(".ask-user-card-attached::before {"))]
    label_start = styles.index(".ask-user-option-label {", styles.index(".ask-user-option-label,"))
    label_block = styles[label_start:styles.index(".ask-user-option-desc {", label_start)]
    assert "color: var(--fg);" in question_block
    assert "max-width: 85%;" in card_block
    assert "isolation: isolate;" in card_block
    assert "margin-left: 8px;" in attached_block
    assert "width: 85%;" in attached_block
    assert "max-width: 85%;" in attached_block
    assert "z-index: -1;" in connector_block
    assert "color: var(--accent, var(--red));" in label_block
    assert "font-size: 11px;" in styles
    assert "font-size: 11px !important;" in styles
    assert "background: var(--ai-bubble-bg, var(--panel));" in styles
    assert "background: var(--send-btn-bg, var(--red));" in styles
    assert "background: color-mix(in srgb, var(--fg) 4%, var(--panel));" not in styles
    assert ".ask-user-other-input" in styles
    assert ".ask-user-answer" in styles
    assert ".ask-user-answer-value" in styles
    assert ".ask-user-card-attached::before" in styles
    assert ".agent-thread.has-ask-user-bottom::before" in styles


def test_scroll_bottom_button_uses_dropdown_caret_glyph():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static/index.html").read_text(encoding="utf-8")
    styles = app_css()

    assert 'class="scroll-nav-caret"' in html
    assert "&#9662;" in html
    assert ">▼</button>" not in html
    assert ".scroll-nav-caret" in styles
    assert "font-family: Arial, Helvetica, sans-serif;" in styles


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
    assert "function _markAskUserAnswered(card, text)" in renderer
    assert "function _answerAskUserCards(root, text)" in renderer
    assert "if (accepted !== false) _markAskUserAnswered(card, text);" in renderer
    assert "if (accepted !== false) _markAskUserAnswered(card, label);" in renderer
    assert "scope.querySelectorAll('.ask-user-card:not(.ask-user-answered)')" in renderer
    assert "if (role === 'user') _answerAskUserCards(box," in renderer
    assert "if (role === 'user') removeAskUserCards(box);" not in renderer
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
    sources = [
        path.read_text(encoding="utf-8")
        for path in (root / "static").rglob("*")
        if path.suffix in {".js", ".html"}
    ]

    def versions(module_name):
        pattern = re.compile(rf"{re.escape(module_name)}\?v=([A-Za-z0-9_-]+)")
        return [match for source in sources for match in pattern.findall(source)]

    # Every URL for a stateful module must resolve to one ES-module instance.
    # Different query strings create distinct modules and duplicate listeners.
    for module_name in ("chatRenderer.js", "chatStream.js", "chat.js"):
        found = versions(module_name)
        assert found, f"missing cache-busted reference for {module_name}"
        assert len(set(found)) == 1, f"split module graph for {module_name}: {found}"

    # Shared modules must have one URL apiece. ui.js is consistently versioned
    # throughout the graph; the other shared modules remain unversioned.
    for module_name in ("sessions.js", "memory.js", "markdown.js", "models.js"):
        assert any(module_name in source for source in sources)
        assert not versions(module_name), f"split module graph for {module_name}"
    assert len(set(versions("ui.js"))) == 1

    compare_stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")
    compare_vote = (root / "static/js/compare/vote.js").read_text(encoding="utf-8")
    renderer_version = versions("chatRenderer.js")[0]
    assert f"chatRenderer.js?v={renderer_version}" in compare_stream
    assert f"chatRenderer.js?v={renderer_version}" in compare_vote
