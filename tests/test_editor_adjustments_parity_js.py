from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADJ_LAYER = ROOT / "static/js/editor/adjustment-layer.js"
PIXEL_PASS = ROOT / "static/js/editor/fx/pixel-pass.js"
HELPERS = ROOT / "static/js/editor/layer-helpers.js"
POPUP = ROOT / "static/js/editor/fx/adj-popup.js"
ADD_MENU = ROOT / "static/js/editor/ai-tools-misc.js"

NEW_TYPES = [
    "invert",
    "posterize",
    "threshold",
    "photo-filter",
    "channel-mixer",
    "color-lookup",
]


def test_adjustment_types_registered():
    src = ADJ_LAYER.read_text(encoding="utf-8")
    for t in NEW_TYPES:
        assert f"'{t}'" in src or f'"{t}"' in src, t


def test_pixel_pass_implements_new_adjustments():
    src = PIXEL_PASS.read_text(encoding="utf-8")
    for t in NEW_TYPES:
        assert f"'{t}'" in src, t


def test_defaults_icons_and_labels():
    src = HELPERS.read_text(encoding="utf-8")
    for t in NEW_TYPES:
        assert f"case '{t}'" in src, f"default {t}"
        assert f"'{t}':" in src, f"icon/label {t}"


def test_popup_exposes_new_adjustments():
    src = POPUP.read_text(encoding="utf-8")
    for t in NEW_TYPES:
        assert f"type: '{t}'" in src, t
        assert f"type === '{t}'" in src, t
    # Slider handlers for the parameterised additions.
    assert "type === 'posterize'" in src
    assert "type === 'channel-mixer'" in src
    assert "p.constant[ch]" in src


def test_add_layer_menu_lists_new_adjustments():
    src = ADD_MENU.read_text(encoding="utf-8")
    for t in NEW_TYPES:
        assert f'data-adjustment-type="{t}"' in src, t
