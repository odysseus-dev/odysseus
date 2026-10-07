from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = (ROOT / "static/js/chatRenderer.js").read_text()
CHAT = (ROOT / "static/js/chat.js").read_text()


def test_saved_web_search_tool_icon_has_no_action_target():
    start = RENDERER.index("export function getToolActionTarget")
    block = RENDERER[start:RENDERER.index("if (lower === 'web_fetch'", start)]
    assert "return null;" in block
    assert "Open search results" not in block


def test_saved_and_live_web_search_queries_are_plain_text():
    for source in (RENDERER, CHAT):
        start = source.index("if (lower === 'web_search'", source.index("function _toolDisplayInfo"))
        block = source[start:source.index("if (lower === 'web_fetch'", start)]
        assert '<span class="agent-thread-summary">' in block
        assert "agent-thread-summary-link" not in block
        assert "Open search results" not in block
