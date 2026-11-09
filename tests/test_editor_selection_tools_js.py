"""Source-level contract for the Phase 6 selection upgrades:
Quick Selection, Object Selection, Color Range and their wiring.

Follows the conventions of tests/test_editor_marquee_selection_js.py
(source-string integration assertions + sw.js precache check).
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


COLOR_RANGE = read("static/js/editor/tools/color-range.js")
QUICK_SELECT = read("static/js/editor/tools/quick-select.js")
OBJECT_SELECT = read("static/js/editor/tools/object-select.js")
TOOLBAR = read("static/js/editor/build/toolbar.js")
CONTROLS = read("static/js/editor/build/controls.js")
STATE = read("static/js/editor/state.js")
GALLERY = read("static/js/galleryEditor.js")
OPTIONS_BAR = read("static/js/editor/build/options-bar.js")
TOPBAR = read("static/js/editor/build/topbar.js")
WIRE_MENUS = read("static/js/editor/wire-topbar-menus.js")
SW = read("static/sw.js")
LASSO = read("static/js/editor/tools/lasso.js")
WIRE_SEL = read("static/js/editor/wire-selection-controls.js")
SELECT_MASK = read("static/js/editor/select-and-mask.js")


def test_color_range_module_exports_helpers():
    assert "export function hexToRgb(hex)" in COLOR_RANGE
    assert "export function colorRangeMask(srcData, width, height, target, fuzziness)" in COLOR_RANGE


def test_quick_and_object_select_modules_export_factories():
    assert "export function createQuickSelectTool({" in QUICK_SELECT
    assert "export function createObjectSelectTool({" in OBJECT_SELECT
    assert "selectionModeForEvent" in QUICK_SELECT
    assert "selectionModeForEvent" in OBJECT_SELECT


def test_toolbar_declares_new_selection_tools():
    assert "quickselect: { label: 'Quick Select'" in TOOLBAR
    assert "objectselect: { label: 'Object Select'" in TOOLBAR
    assert "['marquee', 'lasso']" in TOOLBAR
    assert "['wand', 'quickselect', 'objectselect', 'sam']" in TOOLBAR


def test_eyedropper_icon_is_a_pipette_not_the_brush():
    # The eyedropper must read as an actual dropper at a glance, so it uses the
    # pipette glyph (bulb + barrel + pointed tip), not a droplet or a brush head.
    assert "m2 22 1-1h3l9-9" in TOOLBAR
    assert "M3 21v-3l9-9" in TOOLBAR
    assert "3.4-3.4a2.1 2.1 0 1 1 3 3" in TOOLBAR


def test_eyedropper_tool_uses_a_live_pipette_cursor():
    # Picking the Eyedropper swaps the canvas cursor for the pipette (tip at the
    # hotspot) instead of leaving the default crosshair.
    assert "const eyedropperCursorSvg = `data:image/svg+xml;utf8,${encodeURIComponent(" in GALLERY
    assert "else if (state.tool === 'eyedropper') cursor = `url(\"${eyedropperCursorSvg}\") 2 22, crosshair`;" in GALLERY


def test_all_toolbar_tool_icons_are_uniform_svgs():
    # Every toolbar tool icon must be an inline SVG (no leftover text-glyph
    # icons like the old crop/transform/bg-remove/sharpen glyphs), so they all
    # render at one size via `.ge-tool-icon svg { width: 15px; height: 15px; }`.
    import re

    icons = re.findall(r"icon:\s*'([^']*)'", TOOLBAR)
    assert len(icons) >= 20
    for icon in icons:
        assert icon.startswith("<svg"), f"tool icon is not an SVG: {icon!r}"
        assert 'viewBox="0 0 24 24"' in icon
    # The former text glyphs are now real vector icons.
    assert "<path d=\"M6 2v14a2 2 0 0 0 2 2h14\"/>" in TOOLBAR  # crop
    assert "<polyline points=\"15 3 21 3 21 9\"/>" in TOOLBAR  # transform
    assert "<circle cx=\"6\" cy=\"6\" r=\"3\"/>" in TOOLBAR  # bg remove (scissors)
    assert "<path d=\"M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z\"/>" in TOOLBAR  # sharpen

    style = read("static/css/13-image-editor.css")
    assert ".ge-tool-icon svg" in style


def test_brush_flyout_lists_brush_presets():
    # The Brush slot's sidebar flyout lists the brush presets (Soft Round,
    # Hard Round, Detail, Pixel), so you can pick a preset straight from the
    # tool button, not only from the options-bar dropdown.
    assert "import { loadBrushPresets } from '../brush-presets.js';" in TOOLBAR
    assert "if (members.includes('brush'))" in TOOLBAR
    assert "loadBrushPresets()" in TOOLBAR
    assert "ge-tool-flyout-preset" in TOOLBAR
    assert "presetItem.dataset.brushPreset = preset.id;" in TOOLBAR
    assert "picker.dispatchEvent(new Event('change', { bubbles: true }));" in TOOLBAR
    style = read("static/css/13-image-editor.css")
    assert ".ge-tool-flyout-preset" in style
    assert ".ge-tool-flyout-heading" in style


def test_control_sections_and_state_fields():
    assert 'id="ge-quickselect-section"' in CONTROLS
    assert 'id="ge-objectselect-section"' in CONTROLS
    assert "quickSelectBrushSize" in STATE
    assert "colorRangeFuzz" in STATE


def test_editor_wires_tools_and_color_range():
    assert "import { createQuickSelectTool } from './editor/tools/quick-select.js';" in GALLERY
    assert "import { createObjectSelectTool } from './editor/tools/object-select.js';" in GALLERY
    assert "colorRangeMask as _colorRangeMask" in GALLERY
    assert "const _quickSelectTool = createQuickSelectTool({" in GALLERY
    assert "const _objectSelectTool = createObjectSelectTool({" in GALLERY
    assert "if (state.tool === 'quickselect') return _quickSelectTool.begin(e);" in GALLERY
    assert "if (state.tool === 'objectselect') return _objectSelectTool.begin(e);" in GALLERY
    assert "if (state.tool === 'quickselect') return _quickSelectTool.drag(e);" in GALLERY
    assert "if (state.tool === 'objectselect') return _objectSelectTool.drag(e);" in GALLERY
    assert "if (state.tool === 'quickselect') return _quickSelectTool.end(e);" in GALLERY
    assert "if (state.tool === 'objectselect') return _objectSelectTool.end(e);" in GALLERY
    assert "async function _colorRangeSelection()" in GALLERY
    assert "colorRange: _colorRangeSelection," in GALLERY
    assert "const toolBtn = (id) => state.container.querySelector(" in GALLERY
    assert '.ge-tool-btn[data-tool="${id}"], .ge-tool-btn[data-tool-option="${id}"]' in GALLERY
    assert "const quickSelectBtn = toolBtn('quickselect');" in GALLERY
    assert "const objectSelectBtn = toolBtn('objectselect');" in GALLERY


def test_options_bar_and_select_menu_entries():
    assert "quickselect:  { modes: null, feather: null }" in OPTIONS_BAR
    assert "objectselect: { modes: null, feather: null }" in OPTIONS_BAR
    assert 'data-selection-action="colorRange"' in TOPBAR
    assert "action === 'colorRange'" in WIRE_MENUS
    assert "colorRange," in WIRE_MENUS


def test_new_modules_are_precached():
    for rel in (
        "/static/js/editor/tools/color-range.js",
        "/static/js/editor/tools/quick-select.js",
        "/static/js/editor/tools/object-select.js",
        "/static/js/editor/select-and-mask.js",
    ):
        assert rel in SW, rel


def test_magnetic_lasso_support():
    assert "buildEdgeField" in LASSO
    assert "snapToEdge" in LASSO
    assert "state.lassoMagnetic" in LASSO
    assert 'id="ge-lasso-magnetic"' in CONTROLS
    assert 'id="ge-lasso-mag-width"' in CONTROLS
    assert 'id="ge-lasso-mag-contrast"' in CONTROLS
    assert "lassoMagnetic: false" in STATE
    assert "lassoMagneticWidth" in STATE
    assert "lassoMagneticContrast" in STATE
    assert "lassoMagnetic" in WIRE_SEL


def test_select_and_mask_workspace():
    assert "export function openSelectAndMask(" in SELECT_MASK
    for token in ("ge-sam-overlay", "ge-sam-preview", "ge-sam-output", "ge-sam-apply", "destination-out"):
        assert token in SELECT_MASK, token
    assert "import { openSelectAndMask } from './editor/select-and-mask.js';" in GALLERY
    assert "function _selectAndMask()" in GALLERY
    assert "selectAndMask: _selectAndMask," in GALLERY
    assert 'data-selection-action="selectAndMask"' in TOPBAR
    assert "action === 'selectAndMask'" in WIRE_MENUS
