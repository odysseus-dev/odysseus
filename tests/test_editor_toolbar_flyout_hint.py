"""Toolbar flyout affordances: the "more tools" corner triangle must survive
face re-renders, and brush-preset rows in the brush flyout show a tip glyph.

The triangle hint used to be injected into the slot FACE element, so any
`setInner(face, id)` re-render (e.g. after picking a flyout member) wiped it and
the slot looked like a single tool. It now lives on the slot wrapper, and the
brush-preset rows carry a soft/hard/pixel disc reflecting the preset tip.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOOLBAR = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_flyout_triangle_is_on_the_slot_not_the_face():
    assert "slotEl.insertAdjacentHTML('beforeend', '<span class=\"ge-tool-flyout-tri\" aria-hidden=\"true\"></span>');" in TOOLBAR
    assert "const tri = slotEl.querySelector('.ge-tool-flyout-tri');" in TOOLBAR
    # the hint must not be re-injected into the face (which setInner replaces)
    assert "face.insertAdjacentHTML('beforeend', '<span class=\"ge-tool-flyout-tri\"" not in TOOLBAR


def test_flyout_triangle_css_is_visible():
    # The element is a generous transparent hit area; the visible mark is
    # painted by ::after so it stays easy to click despite being small.
    i = CSS.index(".ge-tool-flyout-tri {")
    block = CSS[i:i + 460]
    assert "width: 18px;" in block
    assert "height: 18px;" in block
    j = CSS.index(".ge-tool-flyout-tri::after {")
    mark = CSS[j:j + 420]
    assert "border-left: 7px solid transparent;" in mark
    assert "border-bottom: 7px solid var(--fg-muted, currentColor);" in mark
    assert "opacity: 0.7;" in mark


def test_brush_preset_rows_have_a_tip_icon():
    assert "const soft = typeof preset.softness === 'number' ? preset.softness : 100;" in TOOLBAR
    assert "const presetIcon = preset.pixel" in TOOLBAR
    assert "icon.className = 'ge-tool-icon';" in TOOLBAR
    assert 'icon.innerHTML = presetIcon;' in TOOLBAR
    assert "label.className = 'ge-tool-flyout-label';" in TOOLBAR
    assert 'label.textContent = preset.name;' in TOOLBAR


def test_flyout_is_clamped_inside_the_viewport():
    assert "const maxLeft = Math.max(4, window.innerWidth - fw - 4);" in TOOLBAR
    assert "const maxTop = Math.max(4, window.innerHeight - fh - 4);" in TOOLBAR
    assert "if (viewLeft > maxLeft) flyout.style.left = (maxLeft - cbX) + 'px';" in TOOLBAR
    assert "if (viewTop > maxTop) flyout.style.top = (maxTop - cbY) + 'px';" in TOOLBAR


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
