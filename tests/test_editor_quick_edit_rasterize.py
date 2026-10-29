from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"
DOC = ROOT / "docs/QUICKEDIT_COVERAGE.md"

RASTERIZE_BRANCH = "if (/\\brasteri[sz]e\\b/.test(p)) {"


def test_rasterize_branch_runs_the_control_op_and_reports_status():
    src = EDITOR.read_text(encoding="utf-8")
    idx = src.index(RASTERIZE_BRANCH)
    branch = src[idx:idx + 260]
    assert "await _runQuickControlOp('layers.rasterize', {});" in branch
    assert "_setAiCommandStatus('Rasterized the layer.', 'done');" in branch
    assert "return;" in branch


def test_rasterize_is_routed_before_the_layer_and_text_blocks():
    src = EDITOR.read_text(encoding="utf-8")
    rasterize = src.index(RASTERIZE_BRANCH)
    # Must run before the layer-management block (whose guard needs a "layer"
    # word) and before the text-edit block (whose guard would otherwise swallow
    # "rasterize the text" because it mentions "text").
    layer_mgmt = src.index("// --- Layer management commands ---")
    text_edit = src.index("// --- Text edit (deterministic) ---")
    assert rasterize < layer_mgmt < text_edit


def test_layers_rasterize_op_bakes_text_or_placed_layers():
    src = EDITOR.read_text(encoding="utf-8")
    assert "'layers.rasterize': { description:" in src
    op = src.index("'layers.rasterize': { description:")
    body = src[op:op + 420]
    assert "if (l.kind === 'placed' && _rasterizePlacedLayer) _rasterizePlacedLayer(l);" in body
    assert "else if (l.kind === 'text' && _rasterizeTextLayer) _rasterizeTextLayer(l);" in body
    # The bake helpers are imported from layer-helpers.
    assert "rasterizeTextLayer as _rasterizeTextLayer" in src
    assert "rasterizePlacedLayer as _rasterizePlacedLayer" in src


def test_coverage_doc_marks_rasterize_ok():
    doc = DOC.read_text(encoding="utf-8")
    assert "| Layer → Rasterize | \"rasterize the text\" | `layers.rasterize` | ✔ | ✔ | ✔ | OK |" in doc
