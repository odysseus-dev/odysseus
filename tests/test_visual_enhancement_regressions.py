"""Static contracts for the visual-enhancement runtime integrations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
THEME = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
CURSOR = (ROOT / "static" / "js" / "effects" / "cursorTrail.js").read_text(encoding="utf-8")
KEYBOARD = (ROOT / "static" / "js" / "keyboard-shortcuts.js").read_text(encoding="utf-8")
UI = (ROOT / "static" / "js" / "ui.js").read_text(encoding="utf-8")
MODELS = (ROOT / "static" / "js" / "models.js").read_text(encoding="utf-8")
LAYOUT_CSS = (ROOT / "static" / "css" / "_layout.css").read_text(encoding="utf-8")
EXTRAS_CSS = (ROOT / "static" / "css" / "_extras.css").read_text(encoding="utf-8")
CORE_CSS = (ROOT / "static" / "css" / "_core.css").read_text(encoding="utf-8")
SPLIT_APP = (ROOT / "split_app_js.py").read_text(encoding="utf-8")
SPLIT_EMAIL = (ROOT / "split_email_server.py").read_text(encoding="utf-8")


def test_chat_decorations_survive_history_replacement():
    assert "_ensureChatDecorations" in APP
    assert "_chatHistEl.prepend(_scrollProgress)" in APP
    assert "_chatHistEl.append(_typingIndicator)" in APP
    assert "replacingHistory" in APP
    assert "setInterval(_updateTyping, 400)" not in APP


def test_cursor_fx_has_idempotent_start_stop_lifecycle():
    assert "export function setCursorTrailEnabled" in CURSOR
    assert "cancelAnimationFrame(frameId)" in CURSOR
    assert "removeEventListener('mousemove', handleMouseMove)" in CURSOR
    assert "prefers-reduced-motion: reduce" in CURSOR
    assert "setCursorTrailEnabled(cursorTrailToggle.checked)" in THEME
    assert "import('./effects/cursorTrail.js')" not in THEME
    assert "if (!frameId) frameId = requestAnimationFrame(draw)" in CURSOR
    assert "frameId = particles.length ? requestAnimationFrame(draw) : 0" in CURSOR
    start = CURSOR[CURSOR.index("function startCursorTrail()") : CURSOR.index("function stopCursorTrail()")]
    assert "requestAnimationFrame(draw)" not in start


def test_contextual_welcome_text_is_not_overwritten_by_timer():
    assert "setInterval(showNextTip" not in INDEX
    assert "welcomeName.dataset.text = 'Deep Research'" in APP
    assert "welcomeName.dataset.text = 'Nobody'" in APP

    hint = APP[APP.index("async function _syncWelcomeModelHint") : APP.index("function initializeEventListeners")]
    assert hint.index("const hasModel = await _hasUsableChatModel()") < hint.index("contextualWelcomeActive")
    assert "if (contextualWelcomeActive || contextualCopyOwned) return" in hint
    assert "function _welcomeCopyIsContextual()" in MODELS
    assert MODELS.count("if (!_welcomeCopyIsContextual())") == 2


def test_research_welcome_copy_is_owned_by_research_not_group():
    research_start = APP.index("function _syncResearchIndicator")
    group_start = APP.index("function _syncGroupIndicator")
    group_end = APP.index("function _closeCompareIfActive")
    research = APP[research_start:group_start]
    group = APP[group_start:group_end]

    assert "Update welcome screen for research mode" in research
    assert "Deep Research" in research
    assert "Update welcome screen for research mode" not in group


def test_welcome_and_scroll_fab_layering_respect_composer_and_motion():
    assert "#welcome-screen {" in LAYOUT_CSS
    welcome_rule = LAYOUT_CSS[LAYOUT_CSS.index("#welcome-screen {") : LAYOUT_CSS.index("#welcome-screen .welcome-tip")]
    assert "z-index:30" in welcome_rule

    fab_rule = LAYOUT_CSS[LAYOUT_CSS.index(".scroll-nav-btn {") : LAYOUT_CSS.index(".scroll-nav-btn::before")]
    assert "position:fixed" in fab_rule
    assert "z-index:244" in fab_rule
    assert "bottomBtn.style.bottom" in INDEX
    assert "bottomBtn.style.right" in INDEX
    assert "bottomBtn.tabIndex = visible ? 0 : -1" in INDEX
    assert "bottomBtn.setAttribute('aria-hidden', visible ? 'false' : 'true')" in INDEX
    reduced = LAYOUT_CSS[LAYOUT_CSS.index("@media (prefers-reduced-motion: reduce)", LAYOUT_CSS.index("#scroll-bottom-btn.slide-out")) :]
    assert ".scroll-nav-btn { transition: none; }" in reduced


def test_scroll_mask_and_new_animations_respect_state_and_reduced_motion():
    assert "box.classList.toggle('has-more-below', !nearBottom)" in APP
    assert ".chat-history.has-more-below" in LAYOUT_CSS
    reduced = LAYOUT_CSS[LAYOUT_CSS.index("@media (prefers-reduced-motion: reduce)", LAYOUT_CSS.index("@keyframes welcome-shimmer")) :]
    for selector in ("#welcome-screen,", ".sidebar-brand-title,", ".list-item.active-session,", ".ai-typing-dot"):
        assert selector in reduced


def test_toast_countdown_and_hidden_hitbox_cleanup():
    progress = LAYOUT_CSS[LAYOUT_CSS.index(".toast-progress-bar {") : LAYOUT_CSS.index(".toast.error .toast-progress-bar")]
    assert "transform: scaleX(1)" in progress
    assert ".toast-progress-bar--running" in progress
    assert "transform: scaleX(0)" in progress

    mobile_toast = LAYOUT_CSS[LAYOUT_CSS.index("@media (max-width: 768px) {", LAYOUT_CSS.index("body:has(.notes-pane")) :]
    assert "pointer-events: none" in mobile_toast
    assert ".toast.show { pointer-events: auto; }" in mobile_toast
    assert ".toast:not(.show) .toast-close-btn" in LAYOUT_CSS
    hidden_close = LAYOUT_CSS[LAYOUT_CSS.index(".toast:not(.show) .toast-close-btn") : LAYOUT_CSS.index(".toast-close-btn:hover")]
    assert "pointer-events: none" in hidden_close
    assert "function _finishToast(el)" in UI
    assert "el.style.pointerEvents = ''" in UI
    assert UI.count("_finishToast(toastEl)") >= 4
    assert "background: var(--accent-primary, var(--red))" in progress
    assert "color-mix(in srgb, var(--accent-primary, var(--red)) 40%" in LAYOUT_CSS
    assert "color-mix(in srgb, var(--accent-primary, var(--red)) 18%" in LAYOUT_CSS
    assert "color: var(--accent-primary, var(--red))" in (ROOT / "static" / "css" / "_interactions.css").read_text(encoding="utf-8")
    assert "--accent: var(--accent-primary, var(--red))" in CORE_CSS


def test_character_counter_matches_server_request_limit():
    assert 'maxlength="50000"' in INDEX
    assert "const CHAR_LIMIT = 50000" in APP
    assert 'id="char-count-badge" aria-hidden="true"' in INDEX


def test_keyboard_cheatsheet_uses_shared_escape_stack_and_modal_a11y():
    assert "registerMenuDismiss(_closeCheatsheet)" in KEYBOARD
    assert "overlay.setAttribute('aria-modal', 'true')" in KEYBOARD
    assert "_cheatsheetPreviousFocus" in KEYBOARD
    assert "document.getElementById('kb-cheatsheet-overlay')?.remove()" in KEYBOARD


def test_android_connect_uses_native_mode_chooser_when_available():
    assert "typeof bridge.showConnectionMode === 'function'" in APP
    assert "bridge.showConnectionMode()" in APP


def test_unsafe_line_based_app_splitter_is_quarantined_before_writes():
    assert SPLIT_APP.index("raise SystemExit") < SPLIT_APP.index("os.makedirs")
    assert "line-based chunks cannot preserve ES-module scope" in SPLIT_APP


def test_unsafe_line_based_email_splitter_is_quarantined_before_writes():
    assert SPLIT_EMAIL.index("raise SystemExit") < SPLIT_EMAIL.index("os.makedirs")
    assert "separated an MCP" in SPLIT_EMAIL
