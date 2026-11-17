import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CSS = (REPO / "static" / "css" / "13-image-editor.css").read_text(encoding="utf-8")
SW = (REPO / "static" / "sw.js").read_text(encoding="utf-8")


def _block(text, opener):
    idx = text.index(opener)
    end = text.index("}", idx)
    return text[idx:end]


def test_layers_footer_tools_spread_full_width():
    block = _block(CSS, ".ge-layers-footer .ge-layer-header-tools {")
    assert "flex: 1 1 auto;" in block
    assert "justify-content: space-between;" in block


def test_empty_layer_tools_hook_hidden():
    assert ".ge-layers-footer .ge-layer-tools:empty { display: none; }" in CSS


def test_editor_hides_the_dimmed_chat_selector():
    # The dimmed New Chat selector behind the editor is no longer tinted: the
    # editor owns the viewport, so the selector is hidden instead (see 313c3ab).
    assert "body.ge-editor-open .chat-meta-overlay {" in CSS
    block = _block(CSS, "body.ge-editor-open .chat-meta-overlay {")
    assert "display: none !important;" in block


def test_sw_cache_name_is_versioned():
    match = re.search(r"const CACHE_NAME = '([^']+)';", SW)
    assert match
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", match.group(1))
