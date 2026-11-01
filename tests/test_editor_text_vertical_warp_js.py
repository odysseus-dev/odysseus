from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_text_layer_exposes_warp_styles():
    src = read("static/js/editor/text-layer.js")
    assert "export const WARP_STYLES" in src
    for style in ["arc", "arch", "bulge", "flag", "wave", "rise", "fisheye", "inflate", "squeeze", "twist"]:
        assert f"'{style}'" in src
    assert "export function warpCanvas(" in src


def test_text_layer_has_vertical_branch():
    src = read("static/js/editor/text-layer.js")
    assert "vertical: false" in src
    assert "warp: 'none'" in src
    assert "warpAmount: 0" in src
    assert "value.vertical" in src
    assert "warpCanvas(baseTextCanvas(layer.text)" in src


def test_text_controls_have_direction_and_warp():
    src = read("static/js/editor/build/controls.js")
    assert 'id="ge-text-direction"' in src
    assert 'id="ge-text-warp"' in src
    assert 'id="ge-text-warp-amount"' in src


def test_gallery_wires_text_direction_and_warp():
    src = read("static/js/galleryEditor.js")
    assert "'ge-text-direction'" in src
    assert "'ge-text-warp'" in src
    assert "'ge-text-warp-amount'" in src
    assert "text.vertical" in src
    assert "text.warp" in src
