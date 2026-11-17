"""Marker tests for the mobile Quick Edit / chrome fixes.

While the Quick Edit card is open on a phone it owns the bottom of the canvas:
the layers sheet is forced back to its peek height and the floating zoom/Setup
footer is hidden. Also covers the tool-rail overflow glyph (a plain ellipsis,
no separator bar) and the full-contrast "New Chat" header.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/galleryEditor.js").read_text()
CSS = (ROOT / "static/css/13-image-editor.css").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_qe_open_body_class_is_toggled():
    assert "document.body.classList.toggle('ge-qe-open', !!open);" in JS


def test_qe_open_forces_peek_and_hides_footer():
    assert "body.ge-qe-open .ge-editor-footer.ge-footer-floating {" in CSS
    assert "body.ge-qe-open .ge-right-panel:not(.minimized) {" in CSS
    idx = CSS.index("body.ge-qe-open .ge-editor-footer.ge-footer-floating {")
    assert "display: none !important;" in CSS[idx:idx + 160]


def test_chat_meta_is_hidden_in_editor():
    assert "body.ge-editor-open .chat-meta-overlay {" in CSS
    idx = CSS.index("body.ge-editor-open .chat-meta-overlay {")
    assert "display: none !important;" in CSS[idx:idx + 120]


def test_tool_rail_more_has_no_separator_bar():
    lines = CSS.splitlines()
    i = lines.index("  .ge-tool-more {")
    block = "\n".join(lines[i:i + 22])
    assert "background: transparent;" in block
    assert "border-left: 1px solid var(--border, #2a313c);" not in block


def test_dropdown_menus_are_capped_to_the_viewport():
    assert ".ge-filter-menu," in CSS
    idx = CSS.index("max-height: min(560px, 70vh) !important;")
    assert "overflow-y: auto" in CSS[idx:idx + 80]


def test_menu_scroll_thumb_is_present():
    assert ".ge-menu-thumb {" in CSS
    assert "ge-menu-thumb" in JS


def test_dropdown_scrollbar_is_visible():
    assert ".ge-filter-menu::-webkit-scrollbar," in CSS
    assert ".ge-filter-menu::-webkit-scrollbar-thumb," in CSS


def test_long_dropdowns_are_capped():
    assert "max-height: min(560px, 70vh) !important;" in CSS
    assert "overscroll-behavior: contain;" in CSS
    assert ".ge-filter-menu," in CSS


def test_menu_scroll_thumb_exists():
    assert ".ge-menu-thumb {" in CSS
    assert "ge-menu-thumb" in JS
    assert "wireMenuThumb" in JS


def test_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
