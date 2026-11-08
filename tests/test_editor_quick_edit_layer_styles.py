"""Quick Edit adds layer styles / effects (Photoshop Layer ▸ Layer Style, Filter ▸ Layer Effects).

"Add a drop shadow", "add a white stroke", "add a colour overlay" etc. previously
fell through the command box to the keyword planner (which had no intent), so
nothing happened. They now route deterministically to the `layers.addStyle` op.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
SERVER = (ROOT / "routes" / "editor_agent_routes.py").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_style_routing_present():
    assert "// --- Layer ▸ Layer Style effects (deterministic, no model call) ---" in EDITOR
    for t in ("color-overlay", "gradient-overlay", "drop-shadow", "outer-glow", "stroke"):
        assert f"styleType = '{t}'" in EDITOR
    assert "await _runQuickControlOp('layers.addStyle', { type: styleType, params });" in EDITOR


def test_style_routing_precedes_selection_block():
    routing = EDITOR.index("await _runQuickControlOp('layers.addStyle', { type: styleType, params });")
    assert routing < EDITOR.index("// --- Selection commands (deterministic, no model call) ---")


def test_addstyle_op_in_both_catalogs():
    assert "'layers.addStyle':" in EDITOR
    assert '"layers.addStyle"' in SERVER


def test_coverage_doc_marks_layer_effects_ok():
    row = next(line for line in COVERAGE.splitlines() if "Filter → Layer effects" in line)
    assert "OK" in row
    assert "fast path" in row
