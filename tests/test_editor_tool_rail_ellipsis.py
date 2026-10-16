"""Marker test: the mobile tool-rail overflow control shows an ellipsis.

EVAL flagged the '<' '>' chevron; it should match the dock overflow control
(an ellipsis '...').
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_tool_rail_more_is_an_ellipsis():
    assert "toolbarMore.textContent = '\\u2026';" in EDITOR
    assert "toolbarMore.textContent = '\\u203A';" not in EDITOR


def test_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
