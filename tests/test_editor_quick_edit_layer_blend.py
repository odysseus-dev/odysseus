"""Quick Edit sets a layer's blend mode (Photoshop Layers-panel blend dropdown).

"Set the blend mode to multiply" previously fell through the command box to the
keyword planner, which had no intent for it, so nothing happened. It now routes
deterministically to the `layers.setBlend` op.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
SERVER = (ROOT / "routes" / "editor_agent_routes.py").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_blend_routing_present():
    assert "// --- Layer ▸ Blend Mode (Photoshop Layers-panel blend dropdown, deterministic) ---" in EDITOR
    assert "await _runQuickControlOp('layers.setBlend', { blendMode: _hit[1] });" in EDITOR
    assert "if (/\\bblend(?:ing)?\\b/.test(p) && !_looksGenerative(p)) {" in EDITOR


def test_blend_routing_precedes_layer_management():
    routing = EDITOR.index("await _runQuickControlOp('layers.setBlend', { blendMode: _hit[1] });")
    assert routing < EDITOR.index("// --- Layer management commands ---")


def test_setblend_op_in_both_catalogs():
    assert "'layers.setBlend':" in EDITOR
    assert '"layers.setBlend"' in SERVER


def test_coverage_doc_marks_layer_blend_ok():
    row = next(line for line in COVERAGE.splitlines() if "Layer → Blend Mode" in line)
    assert "OK" in row
    assert "fast path" in row
