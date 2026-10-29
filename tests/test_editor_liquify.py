from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIQUIFY = ROOT / "static/js/editor/tools/liquify.js"
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
TOOLBAR = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
SHORTCUTS = (ROOT / "static/js/editor/tool-shortcuts.js").read_text(encoding="utf-8")
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
OPTIONS = (ROOT / "static/js/editor/build/options-bar.js").read_text(encoding="utf-8")
STATE = (ROOT / "static/js/editor/state.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SRC = LIQUIFY.read_text(encoding="utf-8")


def test_liquify_module_exposes_a_warp_brush():
    assert "export function createLiquifyTool({ activeLayer, saveState, composite })" in SRC
    assert "const MODES = new Set(['push', 'twirl', 'pucker', 'bloat', 'smooth']);" in SRC
    assert "saveState('Liquify')" in SRC
    for name in ("begin(event)", "drag(event)", "end()", "cancel()"):
        assert name in SRC
    # Per-stroke undo: the snapshot is taken before the first dab mutates pixels.
    assert SRC.index("saveState('Liquify')") < SRC.index("state.drawing = true")
    # Warp maths for every mode.
    for token in ("function dab(", "sampleBilinear", "average3x3", "strokeTo"):
        assert token in SRC


def test_liquify_state_and_toolbar_entry():
    assert "liquifyMode: 'push'," in STATE
    assert "liquifySize: 120," in STATE
    assert "liquifyStrength: 50," in STATE
    assert "liquify: { label: 'Liquify', icon:" in TOOLBAR
    assert "['smudge', 'dodge', 'burn', 'liquify']" in TOOLBAR
    assert "liquify: 'K'," in SHORTCUTS


def test_liquify_controls_and_options_bar():
    assert 'id="ge-liquify-section"' in CONTROLS
    for value in ("push", "twirl", "pucker", "bloat", "smooth"):
        assert f'<option value="{value}">' in CONTROLS
    assert 'id="ge-liquify-mode"' in CONTROLS
    assert 'id="ge-liquify-size"' in CONTROLS
    assert 'id="ge-liquify-strength"' in CONTROLS
    assert "LIQUIFY_TOOLS[toolId]" in OPTIONS
    assert "makeSelect('Mode', '#ge-liquify-mode')" in OPTIONS
    assert "makeRange('Strength', src.strength)" in OPTIONS
    assert ".ge-liquify-hint {" in CSS


def test_liquify_is_wired_into_the_draw_pipeline():
    assert "import { createLiquifyTool } from './editor/tools/liquify.js';" in EDITOR
    assert "const _liquifyTool = createLiquifyTool({" in EDITOR
    assert "'bucket', 'liquify']" in EDITOR  # pixel-paint rasterize gate
    assert "if (state.tool === 'liquify') return _liquifyTool.begin(e);" in EDITOR
    assert "return _liquifyTool.drag(e);" in EDITOR
    # Liquify hides the native cursor, so the hover pass must keep the size
    # circle visible even though the branch returns before the shared block.
    assert "if (overCanvas) _updateBrushCursor(e);" in EDITOR
    assert "if (state.tool === 'liquify') return _liquifyTool.end(e);" in EDITOR
    # Tool-switch sync: section, color row, circle cursor.
    assert "liquifySection.style.display = toolId === 'liquify' ? '' : 'none';" in EDITOR
    assert "['eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'liquify']" in EDITOR
    assert "'dodge', 'burn', 'liquify'].includes(state.tool)" in EDITOR
    assert "basePx = Number(state.liquifySize) || 120;" in EDITOR


def test_pen_action_row_sits_left_with_aligned_icons():
    assert "#ge-pen-section .ge-control-row.ge-actions { margin-left: 0; }" in CSS
    assert "#ge-pen-commit svg { position: relative; top: 2px; }" in CSS
