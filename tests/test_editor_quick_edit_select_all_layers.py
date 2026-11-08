"""Quick Edit can select every layer (Photoshop Layer ▸ Select All Layers).

Photoshop's Layer ▸ Select ▸ All Layers multi-selects the whole layer stack. The
command box previously had no handling for it, so "select all layers" fell into
the pixel-marquee branch and ran a document selection instead. It now routes to
the same button the Layer menu uses, before the pixel "select all" block.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
PANEL = (ROOT / "static" / "js" / "editor" / "layer-panel.js").read_text(encoding="utf-8")
CONTROLS = (ROOT / "static" / "js" / "editor" / "build" / "controls.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_select_all_layers_routing_present():
    assert r"if (/\bselect\b[^.]*\b(?:all|every|everything)\b[^.]*\blayers?\b/.test(p)) {" in EDITOR
    assert "document.getElementById('ge-select-all-layers')?.click();" in EDITOR
    assert "'Selected all layers.'" in EDITOR


def test_routing_precedes_pixel_select_all():
    layers = EDITOR.index("document.getElementById('ge-select-all-layers')?.click();")
    marker = EDITOR.index("// --- Selection commands (deterministic, no model call) ---")
    assert layers < marker
    assert layers < EDITOR.index("'Selected all.'")


def test_button_is_wired_to_select_all_layers():
    assert 'id="ge-select-all-layers"' in CONTROLS
    assert "document.getElementById('ge-select-all-layers')" in PANEL
    assert "selectAllLayers(state);" in PANEL


def test_coverage_doc_marks_select_all_layers_ok():
    row = next(line for line in COVERAGE.splitlines() if "Layer → Select All" in line)
    assert "OK" in row
    assert "fast path" in row
