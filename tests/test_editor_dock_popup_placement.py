import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
STUDIO = (ROOT / "static/js/editor/panels/studio-panels.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_tab_tear_off_icon_sits_inside_the_tab():
    # The undock pop is absolutely positioned inside the tab item, not a sibling
    # control that takes up its own space next to the tab.
    item_block = CSS[CSS.index(".ge-dock-tab-item {"):CSS.index(".ge-dock-tab-pop {")]
    assert "position: relative;" in item_block
    pop_block = CSS[CSS.index(".ge-dock-tab-pop {"):CSS.index(".ge-dock-tab-pop {") + 400]
    assert "position: absolute;" in pop_block
    assert "right: 1px;" in pop_block


def test_adjustment_grid_button_passes_itself_as_anchor():
    assert "button.addEventListener('click', () => onAddAdjustment(type, button));" in STUDIO


def test_on_add_adjustment_forwards_the_anchor():
    assert "onAddAdjustment: (type, anchor) => {" in GALLERY
    assert "anchor || document.getElementById('ge-add-fx')" in GALLERY


def test_open_adj_popup_prefers_the_clicked_anchor():
    assert "_openAdjPopup(layer, type, anchorEl || row, layer.adjustment);" in GALLERY


def test_sw_cache_name_is_version_agnostic():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m, "sw.js CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
