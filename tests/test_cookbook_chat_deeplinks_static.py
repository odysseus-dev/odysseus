from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cookbook_tool_lists_are_linkified_to_stable_entities():
    markdown = (ROOT / "static/js/markdown.js").read_text()
    assert "function linkifyRawCookbookLists" in markdown
    assert "#cookbook-session-${sessionId}" in markdown
    assert "#cookbook-model-${repo}" in markdown
    assert "linkifyRawCookbookLists(s)" in markdown


def test_cookbook_links_open_the_right_tab_and_focus_the_item():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text()
    cookbook = (ROOT / "static/js/cookbook.js").read_text()
    assert "kind === 'cookbook'" in renderer
    assert "focusSession" in renderer
    assert "focusRepo" in renderer
    assert "data-session-id" in cookbook
    assert "data-repo" in cookbook
    assert "cookbook-chat-target" in cookbook
