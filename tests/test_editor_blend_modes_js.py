from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BLEND = ROOT / "static/js/editor/blend-modes.js"
LAYER_CLIPPING = ROOT / "static/js/editor/layer-clipping.js"
ADJUSTMENT_LAYER = ROOT / "static/js/editor/adjustment-layer.js"
LAYER_GROUPS = ROOT / "static/js/editor/layer-groups.js"
CONTROLS = ROOT / "static/js/editor/build/controls.js"
CODEC = ROOT / "static/js/editor/document-codec.js"
SW = ROOT / "static/sw.js"

EXTRA_MODES = [
    "dissolve",
    "linear-burn",
    "linear-dodge",
    "darker-color",
    "lighter-color",
    "vivid-light",
    "linear-light",
    "pin-light",
    "hard-mix",
    "subtract",
    "divide",
]


def test_blend_modes_module_shape():
    src = BLEND.read_text(encoding="utf-8")
    assert "export const NATIVE_BLEND_MODES" in src
    assert "export const EXTRA_BLEND_MODES" in src
    assert "export function blendDraw" in src
    assert "export function isNativeBlendMode" in src
    # Canvas natively supports linear-dodge via 'lighter'.
    assert "linear-dodge" in src and "lighter" in src


def test_extra_blend_modes_are_declared():
    src = BLEND.read_text(encoding="utf-8")
    for mode in EXTRA_MODES:
        assert f"'{mode}'" in src or f'"{mode}"' in src, mode


def test_blend_draw_is_wired_into_all_draw_paths():
    for path in (LAYER_CLIPPING, ADJUSTMENT_LAYER, LAYER_GROUPS):
        src = path.read_text(encoding="utf-8")
        assert "blendDraw" in src, path
        assert "blendDraw(" in src, path


def test_blend_select_and_codec_offer_extra_modes():
    controls = CONTROLS.read_text(encoding="utf-8")
    codec = CODEC.read_text(encoding="utf-8")
    for mode in EXTRA_MODES:
        assert f'value="{mode}"' in controls, mode
        assert f"'{mode}'" in codec or f'"{mode}"' in codec, mode


def test_blend_modes_precached():
    assert "/static/js/editor/blend-modes.js" in SW.read_text(encoding="utf-8")
