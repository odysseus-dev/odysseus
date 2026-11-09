"""Mobile layer-row layout: compact rows + single-line active action strip.

At phone widths the layers sheet gave every idle row a 102px height (a reserved
empty grid track) and wrapped the active row's action strip onto a second line,
where it overflowed onto the next layer. These tests pin the CSS fixes.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_mobile_layer_rows_use_two_tracks():
    # Both the layer-item and group-row rules drop the reserved third track.
    assert CSS.count("grid-template-rows: 44px auto;") >= 2
    assert "grid-template-rows: 40px auto 40px;" not in CSS


def test_active_action_strip_does_not_wrap():
    marker = (
        ".ge-right-panel .ge-layers-list > .ge-layer-item"
        ":not(.ge-adj-sub-item):not(.ge-mask-sub-item).active > .ge-layer-controls"
    )
    assert marker in CSS
    idx = CSS.index(marker)
    block = CSS[idx : idx + 400]
    assert "flex-wrap: nowrap;" in block
    assert "overflow-x: auto;" in block


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache.
    import re
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
