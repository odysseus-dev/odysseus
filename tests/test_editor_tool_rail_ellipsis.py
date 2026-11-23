"""Marker test: the mobile tool-rail overflow control shows a '>' chevron.

Superseded 2026-10-14: the maintainer asked for a '>' chevron instead of the
ellipsis, so it clearly reads as "more tools" rather than a menu.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()


def test_tool_rail_more_is_a_chevron():
    assert "toolbarMore.textContent = '\\u203A';" in EDITOR
    assert "toolbarMore.textContent = '\\u2026';" not in EDITOR
