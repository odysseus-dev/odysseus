from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


BOOLEAN = read("static/js/editor/shape-boolean.js")
TOPBAR = read("static/js/editor/build/topbar.js")
WIRE = read("static/js/editor/wire-extra-menus.js")
GALLERY = read("static/js/galleryEditor.js")
SW = read("static/sw.js")


def test_boolean_module_exports_ops():
    assert "export function combineLayers(" in BOOLEAN
    assert "SHAPE_BOOLEAN_OPS" in BOOLEAN
    for op in ("'unite'", "'subtract'", "'intersect'", "'exclude'"):
        assert op in BOOLEAN
    assert "destination-out" in BOOLEAN
    assert "destination-in" in BOOLEAN


def test_topbar_layer_menu_exposes_combine_actions():
    for action in (
        'data-layer-action="combine-unite"',
        'data-layer-action="combine-subtract"',
        'data-layer-action="combine-intersect"',
        'data-layer-action="combine-exclude"',
    ):
        assert action in TOPBAR


def test_wire_dispatches_combine_actions():
    assert "combineShapes" in WIRE
    assert "combineShapes?.('unite')" in WIRE
    assert "combineShapes?.('subtract')" in WIRE
    assert "combineShapes?.('intersect')" in WIRE
    assert "combineShapes?.('exclude')" in WIRE


def test_gallery_implements_combine_shapes():
    assert "from './editor/shape-boolean.js';" in GALLERY
    assert "_combineSelectedShapes" in GALLERY
    assert "combineShapes: (op) => _combineSelectedShapes(op)" in GALLERY
    assert "_combineLayers(" in GALLERY


def test_boolean_module_is_precached():
    assert "/static/js/editor/shape-boolean.js" in SW
