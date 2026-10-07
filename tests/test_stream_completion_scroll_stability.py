from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
UI = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_terminal_and_canonical_renders_preserve_chat_scroll_anchor():
    canonical = CHAT.split(
        "async function _replaceLiveTurnWithSavedAssistantMessage", 1
    )[1].split("function _ensureStreamLayout", 1)[0]

    assert "const scrollSnapshot = uiModule.captureHistoryScroll?.()" in canonical
    assert "uiModule.restoreHistoryScroll?.(scrollSnapshot)" in canonical
    assert "uiModule.scrollHistory()" not in canonical
    assert "_terminalScrollSnapshot = uiModule.captureHistoryScroll?.()" in CHAT
    assert "restoreHistoryScroll?.(_terminalScrollSnapshot)" in CHAT


def test_streamed_turn_has_a_real_marker_for_canonical_replacement():
    assert "let _streamTurnMarker = document.createComment('live-stream-turn')" in CHAT
    assert "box.appendChild(_streamTurnMarker)" in CHAT
    assert "let _streamTurnMarker = null" not in CHAT


def test_plain_stream_completion_keeps_the_live_response_node():
    terminal = CHAT.split(
        "const _needsCanonicalTurnRebuild", 1
    )[1].split("} // end if (!_isBgFinal)", 1)[0]

    assert "lastToolThread" in terminal
    assert "terminalFinalResponseRendered" in terminal
    assert "_generatedImagesForTurn.length" in terminal
    assert "if (_needsCanonicalTurnRebuild)" in terminal
    assert "_streamTurnMarker.remove()" in terminal


def test_scroll_restoration_cancels_the_stale_smooth_scroll_target():
    restore = UI.split("export function restoreHistoryScroll", 1)[1].split(
        "export function scrollHistoryInstant", 1
    )[0]

    assert "cancelAnimationFrame(_scrollRafId)" in restore
    assert "snapshot.stickToBottom" in restore
    assert "snapshot.scrollTop" in restore
    assert "overflow-anchor: none" in STYLE


def test_large_tool_output_does_not_abort_enabled_auto_scroll():
    """Expanded browser/tool cards may add far more than 300px at once.

    User intent is represented by ``autoScrollEnabled`` (wheel/touch/scroll
    handlers turn it off).  Geometry growth is not evidence that the user
    scrolled away, so synthesis below a large tool timeline must still be
    brought into view.
    """
    smooth_step = UI.split("function _smoothScrollStep()", 1)[1].split(
        "/**\n * Instant scroll to bottom", 1
    )[0]

    assert "!autoScrollEnabled" in smooth_step
    assert "diff > 300" not in smooth_step
    assert "box.scrollTop = current + diff * factor" in smooth_step


def test_programmatic_smooth_scroll_does_not_disable_itself():
    """The scroll event emitted by the lerp is not user intent."""
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    listener = app.split(
        "el('chat-history').addEventListener('scroll', uiModule.debounce", 1
    )[1].split("}, 100));", 1)[0]

    assert "uiModule.isAutoScrolling?.()" in listener
    assert listener.index("uiModule.isAutoScrolling?.()") < listener.index(
        "uiModule.setAutoScroll(atBottom)"
    )
    assert "export function isAutoScrolling()" in UI


def test_large_tool_scroll_fix_is_served_under_a_fresh_chat_module_key():
    """The fixed ui module is imported by chat.js, so stale chat.js is stale UI."""
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    match = re.search(r"chat\.js\?v=([A-Za-z0-9_-]+)", app)
    assert match is not None, "chat.js must be imported with a cache-busting key"
    key = f"chat.js?v={match.group(1)}"

    assert key in app
    assert index.count(key) == 2


def test_stream_completion_does_not_focus_behind_open_document():
    assert "else if (!document.getElementById('doc-editor-pane'))" in CHAT
    assert "messageInput.focus({ preventScroll: true })" in CHAT


def test_chat_and_app_share_one_session_module_instance():
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")

    assert "from './js/sessions.js';" in app
    assert "sessions.js?v=" not in app
    assert 'href="/static/js/sessions.js"' in index
    assert 'src="/static/js/sessions.js"' in index
    assert "'/static/js/sessions.js'" in service_worker
