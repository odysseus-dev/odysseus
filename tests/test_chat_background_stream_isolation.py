import re
from pathlib import Path


CHAT_JS = Path("static/js/chat.js")
CHAT_STREAM_JS = Path("static/js/chatStream.js")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_background_stream_render_helper_refuses_visible_dom_for_other_session():
    source = _source(CHAT_JS)
    render_match = re.search(r"(?m)^\s*_renderStream\s*=\s*\([^)]*\)\s*=>\s*\{", source)
    assert render_match, "expected _renderStream arrow assignment"
    render_start = render_match.start()
    render_prefix = source[render_start : render_start + 420]

    assert "sessionModule.getCurrentSessionId() !== streamSessionId" in render_prefix
    assert "return;" in render_prefix
    assert "!roundHolder || !roundHolder.isConnected" in render_prefix


def test_generated_images_are_session_scoped_during_streaming():
    source = _source(CHAT_JS)
    helper_start = source.index("  function _appendGeneratedImageBubble(data, sessionId = null)")
    helper_prefix = source[helper_start : helper_start + 700]

    assert "targetSessionId" in helper_prefix
    assert "sessionModule.getCurrentSessionId() !== targetSessionId" in helper_prefix
    assert "return false;" in helper_prefix

    calls = re.findall(r"_appendGeneratedImageBubble\(([^\n;]+)\)", source)
    assert calls, "expected generated-image bubble call sites"
    unsafe_calls = [call for call in calls if "streamSessionId" not in call and not call.strip().startswith("data, sessionId")]
    assert unsafe_calls == []


def test_background_completion_toast_does_not_insert_into_other_chat():
    source = _source(CHAT_STREAM_JS)
    fn_start = source.index("export function insertStreamDoneToast(sessionId, query)")
    fn_prefix = source[fn_start : fn_start + 520]

    assert "sessionModule.getCurrentSessionId() !== sessionId" in fn_prefix
    assert "uiModule.showToast" in fn_prefix
    guard_pos = fn_prefix.index("sessionModule.getCurrentSessionId() !== sessionId")
    append_pos = fn_prefix.index("document.getElementById('chat-history')")
    assert guard_pos < append_pos


def test_background_final_and_catch_paths_do_not_cleanup_visible_chat():
    source = _source(CHAT_JS)

    final_start = source.index("      // --- Final render (skip if stream was ever backgrounded")
    final_block = source[final_start : final_start + 900]
    assert "const _isBgFinal" in final_block
    assert "if (!_isBgFinal) {" in final_block
    assert final_block.index("if (!_isBgFinal) {") < final_block.index("_renderStream();")
    assert "document.querySelectorAll('#chat-history .agent-thread.streaming')" in final_block

    catch_start = source.index("      const _isBgCatch =")
    catch_cleanup = source.index("document.querySelectorAll('#chat-history .agent-thread.streaming')", catch_start)
    catch_block = source[catch_start : catch_cleanup + 160]
    assert "if (!_isBgCatch) {" in catch_block
    assert catch_block.index("if (!_isBgCatch) {") < catch_block.index("document.querySelectorAll")
    assert "document.querySelectorAll('#chat-history .agent-thread.streaming')" in catch_block
