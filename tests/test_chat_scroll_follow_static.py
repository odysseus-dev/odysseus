from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
UI = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")
APP = (ROOT / "static/app.js").read_text(encoding="utf-8")
TTS = (ROOT / "static/js/tts-ai.js").read_text(encoding="utf-8")
INDEX = (ROOT / "static/index.html").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")
STYLE = app_css()
CHAT_VERSION = "20261105datefix2"
RENDERER_VERSION = "20261105ttsscroll1"
SHARED_VERSION = "20261104ttspause1"
TTS_VERSION = "20261105ttsscroll1"
CSS_VERSION = "20261105followupleft"
APP_VERSION = CSS_VERSION


def test_ui_distinguishes_programmatic_scroll_and_uses_a_near_bottom_threshold():
    assert "const NEAR_BOTTOM_PX = 120;" in UI
    assert "export function isProgrammaticScroll()" in UI
    assert "export function beginProgrammaticScroll" in UI
    assert "export function endProgrammaticScroll" in UI
    assert "export function isNearHistoryBottom" in UI
    assert "snapshot.following !== false" in UI

    smooth = UI.split("function _smoothScrollStep()", 1)[1].split(
        "/**\n * Instant scroll to bottom", 1
    )[0]
    assert "_markProgrammaticScroll(200)" in smooth

    restore = UI.split("export function restoreHistoryScroll", 1)[1].split(
        "export function scrollHistoryInstant", 1
    )[0]
    assert "_markProgrammaticScroll(300)" in restore


def test_restore_history_scroll_never_repins_after_the_user_scrolls_up():
    restore = UI.split("export function restoreHistoryScroll", 1)[1].split(
        "function _smoothScrollStep", 1
    )[0]

    # Current follow state is the authority, not the pre-render snapshot: a
    # wheel/touch/scrollbar gesture during an async final/canonical render
    # turns follow off and must be left alone.
    assert "const shouldPin = autoScrollEnabled" in restore
    assert "snapshot.following !== false" in restore
    assert "if (!shouldPin) return;" in restore

    apply = restore.split("const apply = () => {", 1)[1].split("};", 1)[0]
    assert "!autoScrollEnabled" in apply
    assert "_markProgrammaticScroll(300)" in apply
    assert "snapshot.scrollTop" not in restore


def test_auto_scroll_pauses_while_the_document_is_hidden():
    scroll_fn = UI.split("export function scrollHistory(options = {})", 1)[1].split(
        "export function captureHistoryScroll", 1
    )[0]
    smooth = UI.split("function _smoothScrollStep()", 1)[1].split(
        "/**\n * Instant scroll to bottom", 1
    )[0]

    assert "document.visibilityState === 'hidden'" in scroll_fn
    assert "document.visibilityState === 'hidden'" in smooth


def test_thinking_collapse_owns_its_scroll_and_repins_when_still_following():
    collapse = CHAT.split("function _collapseLiveThinkingSmooth", 1)[1].split(
        "// Shared teardown", 1
    )[0]

    assert "wasFollowing" in collapse
    assert "uiModule.beginProgrammaticScroll?.(1000)" in collapse
    assert "uiModule.endProgrammaticScroll?.(400)" in collapse
    assert "_repinChatIfFollowing({ force: true })" in collapse
    # The 300px expanded drawer has internal scroll; scrollHeight would snapshot
    # the full hidden transcript and snap the drawer open before folding it.
    assert "content.getBoundingClientRect().height" in collapse
    assert "const start = content.scrollHeight;" not in collapse


def test_failed_tool_status_keeps_the_highlight_color():
    assert ".agent-thread-node.error .agent-thread-icon" not in STYLE
    assert ".agent-thread-node.error .agent-thread-dot" not in STYLE


def test_scroll_listener_ignores_programmatic_events_and_keeps_following():
    listener = APP.split(
        "el('chat-history').addEventListener('scroll', uiModule.debounce", 1
    )[1].split("}, 100));", 1)[0]

    assert "uiModule.isAutoScrolling?.()" in listener
    assert "uiModule.isProgrammaticScroll?.()" in listener
    assert "atBottom" in listener
    assert "uiModule.setAutoScroll(atBottom)" in listener
    assert "performance.now() < _userScrollIntentUntil" in listener


def test_scrollbar_drag_disables_follow_immediately():
    pointer = APP.split(
        "_chatHistoryEl.addEventListener('pointerdown'", 1
    )[1].split("}, { passive: true });", 1)[0]

    assert "_noteUserScrollIntent(2500)" in pointer
    assert "uiModule.setAutoScroll(false)" in pointer


def test_tool_status_final_and_rounds_exhausted_repin_only_when_following():
    helper = CHAT.split("function _repinChatIfFollowing", 1)[1].split(
        "// Smoothly fold", 1
    )[0]
    assert "uiModule.getAutoScroll" in helper
    assert "uiModule.scrollHistory?.({ force })" in helper

    # final_response, tool_start, tool_output, agent_step, terminal finalize
    # and the saved/canonical replacement each re-pin.
    assert CHAT.count("_repinChatIfFollowing({ force: true })") >= 7
    canonical = CHAT.split(
        "async function _replaceLiveTurnWithSavedAssistantMessage", 1
    )[1].split("function _ensureStreamLayout", 1)[0]
    assert "_repinChatIfFollowing({ force: true })" in canonical

    # Direct scrollIntoView for the step-limit card must not bypass follow.
    rounds = CHAT.split("json.type === 'rounds_exhausted'", 1)[1].split(
        "} else if (json.type === 'model_actual')", 1
    )[0]
    assert "if (uiModule.getAutoScroll?.() !== false) {" in rounds
    assert "note.scrollIntoView" in rounds


def test_tts_readalong_respects_user_scroll_and_marks_its_scroll_programmatic():
    block = TTS.split("_scrollReadalongWord(word) {", 1)[1].split(
        "_stopReadalong() {", 1
    )[0]
    assert "typeof ui.getAutoScroll === 'function' && !ui.getAutoScroll()" in block
    assert "document.visibilityState === 'hidden'" in block
    assert "if (this._readalongHasNewerMessage()) return;" in block
    assert "nextElementSibling" in block
    assert "classList.contains('msg')" in block
    assert "this._markReadalongScroll();" in block

    mark = TTS.split("_markReadalongScroll(ms = 900) {", 1)[1].split(
        "_endReadalongScroll() {", 1
    )[0]
    assert "ui.beginProgrammaticScroll?.(ms)" in mark
    assert "clearTimeout(this._readalongScrollTimer)" in mark


def test_scroll_to_bottom_button_reacts_to_streamed_text_and_tts_state():
    assert "characterData:true" in INDEX
    assert "odysseus-agent-working-change" in INDEX
    assert "window.addEventListener('odysseus-tts-state-change', update)" in INDEX
    # TTS Mode must be reachable so auto-play/read-along can be enabled; JS hides
    # it only when no provider is configured.
    assert 'id="overflow-tts-btn" style="display:none"' in INDEX
    assert 'id="overflow-tts-btn" hidden' not in INDEX


def test_scroll_to_bottom_button_is_removed_but_legacy_scroll_button_stays():
    assert 'id="chat-scroll-bottom"' not in INDEX
    assert ".chat-scroll-bottom" not in STYLE
    assert "chat-scroll-bottom" not in APP
    assert "_scrollBottomBtn" not in APP
    assert "odysseus:autoscrollchange" not in APP
    assert "odysseus:autoscrollchange" not in UI

    # The separate, pre-existing affordance must remain untouched.
    assert 'id="scroll-bottom-btn"' in INDEX
    assert "scroll-bottom-btn" in STYLE


def test_scroll_fix_is_served_under_fresh_cache_keys():
    versions = {
        "chat.js": CHAT_VERSION,
        "chatRenderer.js": RENDERER_VERSION,
        "ui.js": SHARED_VERSION,
        "tts-ai.js": TTS_VERSION,
    }
    for module, version in versions.items():
        key = f"{module}?v={version}"
        assert key in APP, f"{module} import is not cache-busted"
        assert key in INDEX, f"{module} script tag is not cache-busted"
        assert key in SW, f"{module} is missing from the service-worker precache"

    assert f"03-agent-chat.css?v={CSS_VERSION}" in INDEX
    assert f"03-agent-chat.css?v={CSS_VERSION}" in SW
    assert f"/static/app.js?v={APP_VERSION}" in INDEX
    assert f"/static/app.js?v={APP_VERSION}" in SW
