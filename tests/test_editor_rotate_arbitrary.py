from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _slice(source, start, end):
    return source[source.index(start):source.index(end)]


def test_geometry_has_a_canvas_expanding_renderer():
    geometry = _read("static/js/editor", "document-geometry.js")
    render = _slice(geometry, "function renderRotatedArbitrary", "function renderFlipped")

    # Bounding-box expansion for an arbitrary angle.
    assert "const width = Math.abs(source.width * cos) + Math.abs(source.height * sin);" in render
    assert "const height = Math.abs(source.width * sin) + Math.abs(source.height * cos);" in render
    assert "const out = makeCanvas(Math.ceil(width), Math.ceil(height));" in render
    # Smooth resampling and a degenerate-source guard.
    assert "ctx.imageSmoothingEnabled = true;" in render
    assert "ctx.imageSmoothingQuality = 'high';" in render
    assert "if (source && source.width > 0 && source.height > 0)" in render
    assert "ctx.translate(out.width / 2, out.height / 2);" in render
    assert "ctx.rotate(radians);" in render
    assert "ctx.drawImage(source, -source.width / 2, -source.height / 2);" in render


def test_geometry_exports_arbitrary_rotation_with_right_angle_delegation():
    geometry = _read("static/js/editor", "document-geometry.js")
    rotate = _slice(geometry, "export function rotateDocumentArbitrary", "/** Flip every layer")

    assert "export function rotateDocumentArbitrary(editorState, degrees) {" in rotate
    assert "const normalized = ((raw % 360) + 360) % 360;" in rotate
    # No-op and exact right angles fall back to the existing integer path.
    assert "if (normalized === 0) {" in rotate
    assert "if ([90, 180, 270].includes(normalized)) return rotateDocument(editorState, normalized);" in rotate
    # Centre-anchored mapping into the expanded canvas.
    assert "const mapPoint = (x, y) => ({" in rotate
    assert "const newWidth = Math.max(1, Math.ceil(Math.abs(oldWidth * cos) + Math.abs(oldHeight * sin)));" in rotate
    # Non-destructive layers keep their own transform; rasters are resampled.
    assert "rotateTextLayer(layer, normalized);" in rotate
    assert "rotateShapeLayer(layer, normalized);" in rotate
    assert "layer.placed = transformPlacedData(layer.placed, matrix);" in rotate
    assert "replaceCanvas(layer, renderRotatedArbitrary(layer.canvas, normalized));" in rotate
    assert "editorState.layerOffsets.set(layer.id, placedOffset || {" in rotate
    assert "invalidateLayerCaches(layer);" in rotate
    # Masks, selections and seeds travel with the pixels.
    assert "mask.space || (mask.mode === 'layer' ? 'layer' : 'document')" in rotate
    assert "replaceStandaloneCanvas(editorState.wandMask, renderRotatedArbitrary(editorState.wandMask, normalized));" in rotate
    assert "editorState.lassoPoints = (editorState.lassoPoints || []).map(point => mapPoint(point.x, point.y));" in rotate
    # Guides can't stay axis-aligned after an arbitrary rotation.
    assert "editorState.guides = { vertical: [], horizontal: [] };" in rotate
    assert "syncDocumentCanvas(editorState, newWidth, newHeight);" in rotate
    assert "return { width: newWidth, height: newHeight, degrees: normalized };" in rotate


def test_canvas_transforms_exposes_rotate_all_arbitrary():
    transforms = _read("static/js/editor", "canvas-transforms.js")
    assert "rotateDocumentArbitrary" in transforms
    method = _slice(transforms, "rotateAllArbitrary(deg)", "flipAll(axis)")
    assert "if (!state.layers.length) return;" in method
    assert "showCanvasLoading('Rotating…');" in method
    assert "const result = rotateDocumentArbitrary(state, deg);" in method
    assert "sizeLabel.textContent = `${result.width}×${result.height}`;" in method
    assert "fitZoom();" in method
    assert "composite();" in method
    assert "hideCanvasLoading();" in method


def test_gallery_editor_wires_the_arbitrary_dep_and_control_op():
    editor = _read("static/js", "galleryEditor.js")
    assert "function _rotateAllLayersArbitrary(deg) { return _canvasTransforms.rotateAllArbitrary(deg); }" in editor
    assert "rotateAllLayersArbitrary: (deg) => _rotateAllLayersArbitrary(deg)," in editor
    op = _slice(editor, "'document.rotateArbitrary':", "'document.flip':")
    assert "Rotate the whole image by an arbitrary angle {degrees}" in op
    assert "if (!Number.isFinite(deg)) throw new Error('degrees required');" in op
    assert "_rotateAllLayersArbitrary(deg);" in op
    assert "return { degrees: deg };" in op


def test_topbar_menu_and_wiring_expose_arbitrary_rotation():
    topbar = _read("static/js/editor", "build", "topbar.js")
    assert 'data-image-action="rotate-arbitrary"' in topbar
    assert "<span>Arbitrary…</span>" in topbar

    wiring = _read("static/js/editor", "wire-topbar-menus.js")
    assert "rotateAllLayers," in wiring
    assert "rotateAllLayersArbitrary," in wiring
    assert "flipAllLayers," in wiring
    assert ("else if (action === 'rotate-arbitrary') promptNumber("
            "'Rotation angle (degrees, clockwise; negative for counter-clockwise)', 15, "
            "(v) => rotateAllLayersArbitrary?.(v));") in wiring
