from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# New P2 (Photoshop/MCP parity) control ops that must be registered in the
# editor control handler map and advertised in the planner catalog.
P2_OPS = [
    'layers.rename', 'layers.setVisibility', 'layers.reorder', 'layers.rasterize',
    'layers.mergeDown', 'layers.flatten', 'layers.align',
    'selection.polygon', 'selection.fill', 'selection.delete', 'selection.copy',
    'selection.paste', 'selection.feather', 'selection.expand', 'selection.contract',
    'text.edit', 'document.crop', 'document.savePsd', 'document.exportLayers',
    'layers.addStyle',
    'document.list', 'document.duplicate', 'document.create', 'document.switch', 'document.openPsd',
    'document.extend',
]


def test_gallery_editor_registers_p2_ops():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    missing = [op for op in P2_OPS if f"'{op}':" not in src]
    assert not missing, f'galleryEditor.js missing P2 ops: {missing}'


def test_planner_catalog_advertises_p2_ops():
    src = (ROOT / 'static/js/editor/nl-edit.js').read_text(encoding='utf-8')
    missing = [op for op in P2_OPS if op not in src]
    assert not missing, f'nl-edit.js OP_CATALOG missing P2 ops: {missing}'


def test_selection_grow_helper_present():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert 'const _growMaskCanvas =' in src
    assert "'selection.expand'" in src and "'selection.contract'" in src


def test_layer_mask_maps_document_selection_by_layer_offset():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "'layers.addMaskFromSelection':" in src
    assert 'const ox = -(off.x || 0)' in src and 'oy = -(off.y || 0)' in src
    assert 'mctx.drawImage(state.wandMask, ox, oy)' in src


def test_paste_uses_params_and_upscale_has_basic_fallback():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "run: (p = {}) => { if (!state.internalClipboard)" in src
    assert 'imageSmoothingQuality' in src and 'basic upscale' in src


def test_run_editor_script_op_present_and_advertised():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    catalog = (ROOT / 'static/js/editor/nl-edit.js').read_text(encoding='utf-8')
    assert "'run_editor_script':" in gallery
    assert 'control.describe(op)' in gallery
    assert 'run_editor_script' in catalog


def test_layer_style_op_present():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "'layers.addStyle':" in gallery
    assert 'function _applyLayerStyleNonInteractive' in gallery


def test_layer_style_op_accepts_retained_effect_types():
    # The agent emits effect types like drop-shadow / stroke / color-overlay that
    # live in effects.js (EFFECT_TYPES), not in LAYER_STYLES; the op must accept both.
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert 'defaultEffectParams as _defaultEffectParams' in gallery
    assert 'EFFECT_TYPES_LIST as _effectTypesList' in gallery
    assert "_effectTypesList.includes(type)" in gallery
    assert 'function _mergeEffectParams' in gallery
    assert "'drop-shadow'" in (ROOT / 'static/js/editor/effects.js').read_text(encoding='utf-8')


def test_adjustments_apply_accepts_model_shorthand():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert 'const _ADJ_SHORTHAND =' in src
    assert 'const _normalizeAdjustmentArgs =' in src
    assert 'p.type || p.adjustment || p.adjustmentType' in src
    assert "p.value != null" in src
    assert "'adjustments.apply': { description: 'Add an adjustment layer and commit it with params ({type,params};" in src


def test_adjustments_apply_clips_to_target_pixel_layer():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    # The agent's adjustments.apply must not bleed onto the document background /
    # letterbox: it is clipped to the target pixel layer and respects its alpha.
    assert 'l.clipped = true;' in src
    assert '_guidedClarity(img.data' in src
    assert 'edgeAware: true' in src
    clipping = (ROOT / 'static/js/editor/layer-clipping.js').read_text(encoding='utf-8')
    assert "if (candidate.kind === 'adjustment') continue;" in clipping
    retouch = (ROOT / 'static/js/editor/retouch.js').read_text(encoding='utf-8')
    assert 'export function guidedClarity' in retouch
    assert 'varI / (varI + eps)' in retouch


def test_retouch_ops_and_agent_wiring_present():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    catalog = (ROOT / 'static/js/editor/nl-edit.js').read_text(encoding='utf-8')
    agent = (ROOT / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    assert "'photo.autoTone':" in gallery
    assert "'retouch.skin':" in gallery and "'retouch.blemish':" in gallery and "'retouch.teeth':" in gallery
    assert "from './editor/retouch.js'" in gallery
    assert 'photo.autoTone' in catalog and 'retouch.skin' in catalog and 'retouch.teeth' in catalog
    assert 'isGenericIntent' in agent and 'formatCropRect' in agent and 'dataUrlDelta' in agent



def test_photo_editor_apply_api_present():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert 'async function gePhotoEditorApply' in src
    assert 'window.gePhotoEditorApply = gePhotoEditorApply;' in src
    assert 'openInEditorAndApply: gePhotoEditorApply' in src
    assert "window.geEditorAgent.run(prompt, { onStep })" in src


def test_document_registry_ops_present():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert 'const _openDocuments =' in gallery
    assert 'const _syncActiveDocument =' in gallery
    assert "_parsePsd(bytes.buffer)" in gallery
    assert "import { parsePsd as _parsePsd" in gallery
    assert 'documentId: null' in (ROOT / 'static/js/editor/state.js').read_text(encoding='utf-8')


def test_psd_payload_round_trip_and_extend_present():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    # _exportLayeredPsd split into a reusable payload builder.
    assert 'function _layeredPsdPayload(' in gallery
    assert "'image/vnd.adobe.photoshop'" in gallery
    # document.savePsd returns the payload (filename/size/dataUrl/layerNames).
    assert 'filename: payload.filename' in gallery or 'filename, size: ' in gallery or '{ filename, size, dataUrl, layerNames }' in gallery
    assert 'layerNames' in gallery
    # openPsd restores real PSD layers via decodeLayeredPsd -> _restoreDraft.
    assert '_decodeLayeredPsd(bytes.buffer)' in gallery
    assert 'decodeLayeredPsd as _decodeLayeredPsd' in gallery
    assert 'layerNames: records.map' in gallery
    # document.extend resizes the canvas with an anchor.
    assert "'document.extend':" in gallery
    assert '_resizeCanvasDocument(state, nw, nh, { anchorX: ax, anchorY: ay })' in gallery


def test_draft_saving_can_be_disabled_for_test_harnesses():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert 'function _draftSavingDisabled(' in gallery
    assert 'window.__geNoDraft' in gallery
    assert 'nodraft=1' in gallery
    assert gallery.count('if (_draftSavingDisabled()) return;') >= 2


def test_resize_canvas_op_resamples_image_content():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "'document.resizeCanvas':" in gallery
    assert 'resizeImageDocument as _resizeImageDocument' in gallery
    assert '_resizeImageDocument(state, nw, nh, {})' in gallery


def test_p3_filters_high_pass_and_lens_blur_present():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "id === 'high-pass'" in src
    assert "return { id: 'high-pass'" in src
    assert "id === 'lens-blur'" in src
    assert "return { id: 'lens-blur'" in src
    assert "'high-pass': 'high-pass'" in src
    assert "'lens-blur': 'lens-blur'" in src


def test_screenshot_op_waits_for_async_composite():
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "while (!state.documentRenderReady && Date.now() < deadline)" in src
    assert "'screenshot': { description: 'Return the current canvas as a PNG data URL (waits" in src


def test_resize_canvas_preserves_aspect_ratio():
    # document.resizeCanvas must not stretch content: if the target aspect
    # differs it crops to that aspect (centred on the subject/selection) first,
    # then scales uniformly. allowDistort opts out.
    src = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    assert "'document.resizeCanvas': { description: 'Resample the whole image to a new pixel size" in src
    assert 'allowDistort' in src
    assert 'const _contentFocusCenter =' in src
    assert 'const _imageLayerBounds =' in src
    assert 'const _layerDocBounds =' in src
    # crop-to-aspect (via the pure aspect-fit helper) then uniform resample.
    assert "import { aspectFitCropRect as _aspectFitCropRect } from './editor/aspect-fit.js'" in src
    assert '_aspectFitCropRect({ width: state.imgWidth, height: state.imgHeight' in src
    assert "_cropDocument(state, { x: rect.x, y: rect.y, width: rect.width, height: rect.height })" in src


def test_text_layout_op_places_titles_in_image_bounds():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    catalog = (ROOT / 'static/js/editor/nl-edit.js').read_text(encoding='utf-8')
    agent = (ROOT / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    assert "'text.layout':" in gallery
    assert 'text.layout' in catalog
    # agent title guard repositions the text layer relative to the image bounds
    assert 'titleIntent' in agent
    assert "control.invoke('text.layout'" in agent
    # local planner adds a layout step after text.add
    assert "op: 'text.layout'" in catalog


def test_text_layout_default_size_and_subject_avoidance():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    # size clamp raised and a larger default for the title
    assert 'Math.min(0.2, Math.max(0.08' in gallery
    # when a subject/face selection exists the top-center title drops to bottom-center
    assert "anchor = 'bottom-center'" in gallery
    assert '_maskAlphaBounds(md, mw, mh, 8)' in gallery
    catalog = (ROOT / 'static/js/editor/nl-edit.js').read_text(encoding='utf-8')
    assert 'sizePct: 0.13' in catalog


def test_escape_guard_closes_floating_dialogs():
    gallery = (ROOT / 'static/js/galleryEditor.js').read_text(encoding='utf-8')
    # the editor Esc guard explicitly closes the floating export dialog,
    # adjustment popup and filter dialog before the modal guard can eat Escape
    assert "document.querySelector('.ge-export-overlay')" in gallery
    assert "document.querySelector('.ge-adj-popup')" in gallery
    assert "document.querySelector('.ge-filter-overlay')" in gallery
    assert ".ge-export-cancel" in gallery
    assert '[data-adj-action="cancel"]' in gallery
