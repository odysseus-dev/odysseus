from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static/css/13-image-editor.css"
SW = ROOT / "static/sw.js"


def _css():
    return CSS.read_text(encoding="utf-8")


def test_mobile_peek_bounds_the_whole_layers_section():
    # The peek (collapsed) mobile sheet must bound the entire layers section,
    # not just the list — otherwise the header/blend/list stop fitting and the
    # add/fx/delete footer hangs below the viewport with no way to reach it.
    css = _css()
    assert ".ge-right-panel:not(.expanded) .ge-layers {" in css
    assert "max-height: var(--peek-height, 110px);" in css


def test_mobile_peek_layers_list_scrolls_internally():
    css = _css()
    assert ".ge-right-panel:not(.expanded) .ge-layers-list {" in css
    assert "flex: 1 1 auto;" in css
    assert "overflow-y: auto;" in css


def test_mobile_peek_layers_list_is_not_centred_capped():
    # The old rule capped the list at (peek - 52px) which let the section
    # grow past the peek. That cap must be gone.
    css = _css()
    assert "max-height: calc(var(--peek-height, 110px) - 52px)" not in css


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache.
    import re
    sw = SW.read_text(encoding="utf-8")
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", sw)
