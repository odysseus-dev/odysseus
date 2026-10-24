from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
AI_MISC = (ROOT / "static/js/editor/ai-tools-misc.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_layer_footer_has_separate_add_layer_and_fx_buttons():
    assert 'id="ge-add-layer"' in CONTROLS
    assert 'id="ge-add-fx"' in CONTROLS
    assert 'class="ge-add-fx-glyph"' in CONTROLS
    # the fx button sits right next to the plain new-layer button
    assert CONTROLS.index('id="ge-add-layer"') < CONTROLS.index('id="ge-add-fx"')


def test_plus_button_adds_a_pixel_layer_directly():
    # ge-add-layer no longer opens a menu, it adds a raster layer immediately
    assert "const addButton = document.getElementById('ge-add-layer');" in AI_MISC
    assert "addButton?.addEventListener('click', event => {" in AI_MISC
    assert "addEmptyLayer();" in AI_MISC
    # the combined pixel+adjustment menu is gone
    assert 'data-layer-kind="raster"' not in AI_MISC
    assert "Pixel Layer" not in AI_MISC


def test_fx_button_opens_the_adjustment_menu():
    assert "const buildFxMenu = (anchor) => {" in AI_MISC
    assert "const addFxButton = document.getElementById('ge-add-fx');" in AI_MISC
    assert "buildFxMenu(addFxButton);" in AI_MISC
    # the fx menu still offers adjustment-layer types through the shared popup
    assert 'data-adjustment-type="curves"' in AI_MISC
    assert "openAdjustmentLayer?.(button.dataset.adjustmentType, anchor)" in AI_MISC
    assert ".ge-add-fx-glyph" in STYLE
