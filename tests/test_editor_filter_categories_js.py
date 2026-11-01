from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EFFECTS = ROOT / "static/js/editor/filters/effects.js"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
WIRE = ROOT / "static/js/editor/wire-topbar-menus.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
SW = ROOT / "static/sw.js"

EXTRA_ACTIONS = [
    "blur-motion",
    "sharpen",
    "sharpen-edges",
    "unsharp-mask",
    "noise-add",
    "noise-despeckle",
    "pixelate-mosaic",
    "pixelate-crystallize",
    "stylize-emboss",
    "stylize-find-edges",
    "stylize-solarize",
    "distort-twirl",
    "distort-pinch",
    "distort-spherize",
    "distort-ripple",
    "render-clouds",
]


def read(path):
    return path.read_text(encoding="utf-8")


def test_effects_module_exports_renderers():
    src = read(EFFECTS)
    for name in (
        "addNoise", "despeckle", "pixelate", "crystallize", "emboss",
        "findEdges", "solarize", "twirl", "pinch", "spherize", "ripple",
        "clouds", "sharpen", "sharpenEdges", "unsharpMask",
    ):
        assert f"export function {name}(" in src, name


def test_gallery_imports_effects_renderers():
    src = read(GALLERY)
    assert "from './editor/filters/effects.js';" in src
    for alias in (
        "_addNoise", "_despeckle", "_pixelate", "_crystallize", "_emboss",
        "_findEdges", "_solarize", "_twirl", "_pinch", "_spherize", "_ripple",
        "_clouds", "_sharpen", "_sharpenEdges", "_unsharpMask",
    ):
        assert alias in src, alias


def test_gallery_defines_apply_wrappers():
    src = read(GALLERY)
    for name in (
        "Noise", "Despeckle", "Pixelate", "Crystallize", "Emboss",
        "FindEdges", "Solarize", "Twirl", "Pinch", "Spherize", "Ripple",
        "Clouds", "Sharpen", "SharpenEdges", "UnsharpMask",
    ):
        assert f"function _apply{name}(" in src, name


def test_gallery_passes_filter_closures():
    src = read(GALLERY)
    for key in (
        "applyMotionBlur", "applyNoise", "applyDespeckle", "applyPixelate",
        "applyCrystallize", "applyEmboss", "applyFindEdges", "applySolarize",
        "applyTwirl", "applyPinch", "applySpherize", "applyRipple",
        "applyClouds", "applySharpen", "applySharpenEdges", "applyUnsharpMask",
    ):
        assert f"{key}: () =>" in src, key


def test_topbar_exposes_filter_actions():
    src = read(TOPBAR)
    for action in EXTRA_ACTIONS:
        assert f'data-filter-action="{action}"' in src, action


def test_wire_dispatch_calls_closures():
    src = read(WIRE)
    for key in (
        "applyMotionBlur", "applyNoise", "applyDespeckle", "applyPixelate",
        "applyCrystallize", "applyEmboss", "applyFindEdges", "applySolarize",
        "applyTwirl", "applyPinch", "applySpherize", "applyRipple",
        "applyClouds", "applySharpen", "applySharpenEdges", "applyUnsharpMask",
    ):
        assert f"{key}?" in src, key


def test_effects_module_is_precached():
    assert "/static/js/editor/filters/effects.js" in read(SW)
