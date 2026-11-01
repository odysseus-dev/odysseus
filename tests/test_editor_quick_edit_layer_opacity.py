"""Quick Edit sets a layer's opacity (Photoshop Layers-panel Opacity field).

"Set the layer opacity to 50%" previously fell through the command box to the
keyword planner, which had no intent for it, so nothing happened. It now routes
deterministically to the `layers.setOpacity` op.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
SERVER = (ROOT / "routes" / "editor_agent_routes.py").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_opacity_routing_present():
    assert "// --- Layer ▸ Opacity (Photoshop Layers-panel Opacity field, deterministic) ---" in EDITOR
    assert "await _runQuickControlOp('layers.setOpacity', { opacity: _val });" in EDITOR
    assert "const _opWord = /\\b(?:opacity|opaque|transparen(?:t|cy))\\b/.test(p);" in EDITOR


def test_opacity_routing_precedes_layer_management():
    routing = EDITOR.index("await _runQuickControlOp('layers.setOpacity', { opacity: _val });")
    assert routing < EDITOR.index("// --- Layer management commands ---")


def test_setopacity_op_in_both_catalogs():
    assert "'layers.setOpacity':" in EDITOR
    assert '"layers.setOpacity"' in SERVER


def test_coverage_doc_marks_layer_opacity_ok():
    row = next(line for line in COVERAGE.splitlines() if "Layer → Opacity" in line)
    assert "OK" in row
    assert "fast path" in row
