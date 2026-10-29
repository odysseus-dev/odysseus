import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_idle_layer_rows_hide_the_opacity_slider():
    assert ".ge-layer-item:not(.ge-adj-sub-item):not(.ge-mask-sub-item) > .ge-layer-opacity-row {\n    display: none;" in CSS
    assert ".ge-layer-item:not(.ge-adj-sub-item):not(.ge-mask-sub-item).active > .ge-layer-opacity-row {\n    display: flex;" in CSS


def test_layer_opacity_slider_is_thin_and_not_full_width():
    assert "body.ge-editor-open .ge-layer-item .ge-layer-opacity {" in CSS
    assert "min-height: 0 !important;" in CSS
    assert "width: 140px !important;" in CSS


def test_layers_footer_stays_on_one_row():
    idx = CSS.rindex(".ge-layers-footer {")
    block = CSS[idx:idx + 400]
    assert "flex-wrap: nowrap;" in block
    assert ".ge-layers-footer #ge-layer-fill {" in CSS


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache, so pinning the exact
    # number makes this test fail on the next commit.
    m = re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
    assert m, "CACHE_NAME not found"
