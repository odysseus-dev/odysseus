from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_text_layer_has_opentype_fields():
    src = read("static/js/editor/text-layer.js")
    for token in [
        "smallCaps: false",
        "kerning: true",
        "ligatures: true",
        "smallCaps: !!value.smallCaps",
        "kerning: value.kerning !== false",
        "ligatures: value.ligatures !== false",
        "applyFontOptions",
        "fontVariantCaps",
        "fontKerning",
    ]:
        assert token in src


def test_text_controls_have_opentype_toggles():
    src = read("static/js/editor/build/controls.js")
    for token in [
        "ge-text-opentype-row",
        'id="ge-text-small-caps"',
        'id="ge-text-kerning"',
        'id="ge-text-ligatures"',
    ]:
        assert token in src


def test_gallery_wires_opentype_toggles():
    src = read("static/js/galleryEditor.js")
    for token in [
        "ge-text-small-caps",
        "ge-text-kerning",
        "ge-text-ligatures",
        "smallCaps",
        "ligatures",
        '.ge-text-opentype-row input[type="checkbox"]',
    ]:
        assert token in src
