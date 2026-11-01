"""Toolbar batch: inline wand refinements, custom crop ratio, dodge/burn
intensity, gradient strip de-duplication, the tall text strip and the pen
overlay that no longer re-composites the whole document."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
STATE = (ROOT / "static/js/editor/state.js").read_text(encoding="utf-8")
PIPE = (ROOT / "static/js/editor/stroke-pipeline.js").read_text(encoding="utf-8")
SLIDERS = (ROOT / "static/js/editor/stroke-tool-sliders.js").read_text(encoding="utf-8")
PEN = (ROOT / "static/js/editor/tools/pen-selection.js").read_text(encoding="utf-8")
OB = (ROOT / "static/js/editor/build/options-bar.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_custom_crop_ratio_field():
    assert 'id="ge-crop-custom-ratio"' in CONTROLS
    assert 'class="ge-crop-custom"' in CONTROLS
    assert ".ge-crop-custom input {" in CSS
    # The typed ratio is applied through the same preset path as the buttons,
    # and a centred rect is created when none is drawn yet.
    assert "customRatioInput.addEventListener('change', applyCustomRatio);" in EDITOR
    assert "if (!(rect && rect.w > 5 && rect.h > 5)) {" in EDITOR


def test_dodge_and_burn_intensity_slider():
    assert 'id="ge-dodgeburn-intensity"' in CONTROLS
    assert "dodgeBurnIntensity: 25," in STATE
    assert "state.dodgeBurnIntensity = parseInt(e.target.value, 10);" in SLIDERS
    assert PIPE.count("state.dodgeBurnIntensity") >= 2
    assert PIPE.count("alpha * intensity") >= 2
    # The strip mirrors Intensity so it is adjustable without the More menu.
    assert "intensity: '#ge-dodgeburn-intensity'" in OB
    assert "makeRange('Intensity', src.intensity)" in OB
    # The row is a real strip row next to Size, shown for Dodge/Burn only.
    assert "if (dodgeBurnRow) dodgeBurnRow.style.display = (toolId === 'dodge' || toolId === 'burn') ? '' : 'none';" in EDITOR
    menu = CONTROLS[CONTROLS.index('id="ge-brush-more-menu"'):]
    assert 'id="ge-dodgeburn-intensity-row"' not in menu.split('ge-brush-pressure-row')[0]
    # Its label is abbreviated (Int) and its column shrinks to the word so the
    # text sits flush against its own slider.
    assert '<label title="Intensity">Int <span id="ge-dodgeburn-intensity-label">25%</span></label>' in CONTROLS
    assert "#ge-dodgeburn-intensity-row label { flex: 0 0 auto; min-width: 20px; }" in CSS
    # The dot and its label sit 10px further right than the row's edge.
    assert "#ge-dodgeburn-intensity-row > .ge-eraser-preview { margin-left: 10px; }" in CSS


def test_every_clear_cross_is_the_same_square_target():
    start = CSS.index("#ge-marquee-clear,\n#ge-wand-clear,")
    block = CSS[start:start + 700]
    assert "  width: 26px;\n  height: 26px;" in block
    assert "  min-width: 26px;\n  min-height: 26px;" in block
    assert "  display: inline-flex;\n  align-items: center;\n  justify-content: center;" in block
    for sel in ("#ge-wand-clear", "#ge-marquee-clear", "#ge-lasso-clear", "#ge-clone-source-clear"):
        assert sel in block


def test_clone_clear_icon_moves_back_when_a_source_is_selected():
    assert "#ge-clone-source-clear.has-selection svg { top: 1px; }" in CSS


def test_gradient_strip_is_not_duplicated():
    # The real #ge-gradient-section renders inside the strip; the mirror must
    # not add a second Type/Opacity pair.
    assert "ge-gradient-type" not in OB
    assert "ge-gradient-opacity" not in OB
    assert 'id="ge-gradient-section"' in CONTROLS


def test_text_strip_can_wrap_tall():
    assert "ge-options-tall" in CSS
    assert "classList.toggle('ge-options-tall', toolId === 'text')" in EDITOR
    assert CSS.count("flex-wrap: wrap !important;") >= 2


def test_pen_tool_paints_its_overlay_without_recompositing():
    assert "state.penOverlay" in PEN
    assert "state.penOverlayCtx" in PEN
    assert "const repaint = () => { if (typeof redraw === 'function') redraw(); else composite(); };" in PEN
    assert PEN.count("repaint();") >= 4
    # begin()/drag() must not re-composite the document
    begin = PEN[PEN.index("begin(event)"):PEN.index("drag(event)")]
    assert "composite()" not in begin
    assert "redraw: () => _penTool.draw()," in EDITOR
    assert ".ge-pen-overlay {" in CSS
    assert "state.penOverlay = null;" in EDITOR
    # The stylesheet hides the overlay by default, so revealing it must set an
    # explicit display rather than clearing the inline value.
    assert "overlay.style.display = 'block';" in PEN
    # Closing the path must show the marching ants. It refreshes just the
    # selection overlay (cheap) and falls back to a full composite.
    assert "if (typeof refreshSelection === 'function') refreshSelection();" in PEN
    assert "else composite();" in PEN
    assert "refreshSelection: () => _refreshSelectionOverlay()," in EDITOR
    # A pen close only changes the selection, so it must not re-read every
    # layer's pixels for the undo snapshot.
    assert "saveState('Pen selection', { selectionOnly: true });" in PEN
    assert "const reuse = options.selectionOnly ? state.undoStack.at(-1) : null;" in EDITOR
    # The preview shows handles only for the anchor being shaped.
    assert "const shaped = dragging ? points.length - 1 : -1;" in PEN


def test_brush_size_readout_hugs_its_slider():
    # The px value IS the whole label on the size row; letting the shared 36px
    # label column force it open leaves a dead gap before its own slider.
    assert ".ge-brush-size-row label { min-width: 0; }" in CSS


def test_sw_cache_name_bumped():
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
