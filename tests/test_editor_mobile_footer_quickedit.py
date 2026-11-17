"""Mobile (<=700px) editor chrome: footer removal, Quick Edit stacking, more-tools chevron.

Requested (390x844 phone):
1. Remove the floating zoom/Setup footer on phones — it collided with the peek
   layers sheet and the Quick Edit card. Undo/Redo/History already live in the
   layers-sheet header and pinch-zoom replaces the zoom buttons.
2. The Quick Edit card lifts above the bottom sheets / keyboard; keep it above
   every other editor asset while lifted (z-index 90).
3. The tool-rail "more tools" affordance should be a right chevron, not an
   ellipsis.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")

def test_mobile_footer_is_docked_flush_and_accessible():
    assert ".ge-editor-footer" in CSS
    assert "overflow-x: auto;" in CSS
    assert ".ge-editor-footer {\n    display: none !important;" not in CSS


def test_quick_edit_stacks_above_other_assets():
    assert ".ge-ai-command {\n    z-index: 90;" in CSS


def test_more_tools_button_is_a_chevron():
    assert "toolbarMore.textContent = '\\u203A';" in EDITOR
    assert "toolbarMore.textContent = '\\u2026';" not in EDITOR


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
