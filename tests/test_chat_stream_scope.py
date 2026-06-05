from pathlib import Path


def test_stream_render_helpers_are_visible_to_catch_block():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    try_start = source.index("    try {\n      // Re-enable auto-scroll")
    catch_start = source.index("    } catch (err) {", try_start)

    outer_scope = source[:try_start]
    try_body = source[try_start:catch_start]

    assert "let _renderStream = () => {};" in outer_scope
    assert "let _cancelThinkingTimer = () => {};" in outer_scope
    assert "let _removeThinkingSpinner = () => {};" in outer_scope

    assert "_renderStream = () => {" in try_body
    assert "_cancelThinkingTimer = () => {" in try_body
    assert "_removeThinkingSpinner = () => {" in try_body
    assert "function _renderStream()" not in try_body
