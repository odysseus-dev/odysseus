from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_patch_engine_is_offline_and_canvas_seam_injected():
    ops = _read("static/js/editor", "patch-ops.js")

    # Pure helpers with a createCanvas seam (like mask-ops.js) — no network.
    assert "export function patchRegion(createCanvas, layerCanvas, maskCanvas, offset, dx, dy, feather = 0)" in ops
    assert "export function contentAwareMove(createCanvas, layerCanvas, maskCanvas, offset, dx, dy, iterations = 16)" in ops
    assert "function restrictToMask(createCanvas, source, mask, width, height)" in ops
    assert "function featherMask(createCanvas, mask, width, height, radius)" in ops
    assert "function maskInLayerSpace(createCanvas, mask, offset, width, height)" in ops
    assert "fetch(" not in ops


def test_patch_region_samples_the_source_through_a_feathered_mask():
    ops = _read("static/js/editor", "patch-ops.js")
    region = ops[ops.index("export function patchRegion"):ops.index("export function contentAwareMove")]

    assert "const shifted = createCanvas(w, h);" in region
    # Sampling at (x+dx, y+dy) == the layer drawn shifted by (-dx, -dy).
    assert "shifted.getContext('2d').drawImage(layerCanvas, -dx, -dy);" in region
    assert "const sourceInMask = restrictToMask(createCanvas, shifted, localMask, w, h);" in region
    assert "if (feather > 0) localMask = featherMask(createCanvas, localMask, w, h, feather);" in region
    assert "octx.drawImage(sourceInMask, 0, 0);" in region


def test_content_aware_move_cuts_pastes_and_diffusion_fills_the_hole():
    ops = _read("static/js/editor", "patch-ops.js")
    move = ops[ops.index("export function contentAwareMove"):]

    assert "const cut = restrictToMask(createCanvas, layerCanvas, localMask, w, h);" in move
    # Hole starts transparent, then the edge colour grows inward.
    assert "fctx.globalCompositeOperation = 'destination-out';" in move
    assert "fctx.drawImage(localMask, 0, 0);" in move
    assert "fctx.globalCompositeOperation = 'destination-over';" in move
    assert "const radius = Math.ceil(Math.max(bw, bh) / 2);" in move
    # Nearest-boundary inward fill: grow one pixel per pass, four directions.
    assert "for (let i = 0; i < passes; i++)" in move
    assert "fctx.drawImage(fill, 1, 0);" in move
    assert "fctx.drawImage(fill, -1, 0);" in move
    assert "fctx.drawImage(fill, 0, 1);" in move
    assert "fctx.drawImage(fill, 0, -1);" in move
    assert "const maskedAvg = restrictToMask(createCanvas, avg, localMask, w, h);" in move
    # Paste the cut content at the offset, on top of the filled base.
    assert "octx.drawImage(filledHole, 0, 0);" in move
    assert "octx.drawImage(cut, dx, dy);" in move


def test_patch_tool_tracks_a_drag_offset_and_applies_on_release():
    tool = _read("static/js/editor", "tools", "patch.js")

    assert "export function createPatchTool({" in tool
    assert "import { state } from '../state.js';" in tool
    assert "showToast?.('Make a selection, then drag the Patch tool to a source area');" in tool
    assert "state.patchDx = Math.round(coords.x - start.x);" in tool
    assert "state.patchDy = Math.round(coords.y - start.y);" in tool
    assert "saveState?.('Patch');" in tool
    assert "onApply?.(dx, dy);" in tool
    assert "return { begin, drag, end, cancel };" in tool


def test_galleryeditor_wires_the_patch_tool_and_ops():
    editor = _read("static/js", "galleryEditor.js")

    assert "import { createPatchTool } from './editor/tools/patch.js';" in editor
    assert "import { patchRegion as _patchRegion, contentAwareMove as _contentAwareMove } from './editor/patch-ops.js';" in editor
    assert "const _patchTool = createPatchTool({" in editor
    assert "function _applyPatchOffset(dx, dy, { history = true } = {})" in editor
    assert "function _applyContentAwareMove(dx, dy, { history = true } = {})" in editor
    assert "_patchRegion(_patchCreateCanvas, layer.canvas, state.wandMask, offset, dx, dy, 4)" in editor
    assert "_contentAwareMove(_patchCreateCanvas, layer.canvas, state.wandMask, offset, dx, dy)" in editor
    # Dispatcher hooks (begin / drag / end) mirror object-selection.
    assert "if (state.tool === 'patch') return _patchTool.begin(e);" in editor
    assert "if (state.tool === 'patch') return _patchTool.drag(e);" in editor
    assert "if (state.tool === 'patch') return _patchTool.end(e);" in editor
    # Control ops.
    assert "'patch.apply': {" in editor
    assert "'edit.contentAwareMove': {" in editor


def test_patch_is_exposed_in_the_toolbar_and_edit_menu():
    toolbar = _read("static/js/editor/build", "toolbar.js")
    topbar = _read("static/js/editor/build", "topbar.js")
    extra = _read("static/js/editor", "wire-extra-menus.js")

    assert "patch: { label: 'Patch'," in toolbar
    assert "['clone', 'heal', 'patch']" in toolbar
    assert 'data-edit-action="content-aware-move"' in topbar
    assert "else if (action === 'content-aware-move')" in extra
    assert "window.geEditorControl?.invoke('edit.contentAwareMove', { dx: Math.round(parts[0]), dy: Math.round(parts[1]) });" in extra
