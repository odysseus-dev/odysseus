from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()
LASSO = (ROOT / "static/js/editor/tools/lasso.js").read_text()
STROKE = (ROOT / "static/js/editor/tools/stroke.js").read_text()
CANVAS_EVENTS = (ROOT / "static/js/editor/canvas-events.js").read_text()


def test_escape_cancels_transient_editor_tools_before_editor_guard():
    crop = EDITOR.index("if (state.cropRect || state.cropping || state.cropMoving)")
    marquee = EDITOR.index("if (state.marqueeActive || state.selectionMoving)", crop)
    lasso = EDITOR.index("if (state.lassoActive || state.lassoPoints.length)", marquee)
    gradient = EDITOR.index("if (state.gradientActive)", lasso)
    stroke = EDITOR.index("if (state.drawing)", gradient)
    shape = EDITOR.index("if (_shapeDraft)", stroke)
    guard = EDITOR.index("if (window.__galleryEditLive || _galleryEditMounted())", shape)
    assert crop < marquee < lasso < gradient < stroke < shape < guard
    assert "_marqueeTool.cancel('escape')" in EDITOR
    assert "_lassoTool.cancel();" in EDITOR
    assert "_gradientTool.cancel();" in EDITOR
    assert "_strokeTool.cancel();" in EDITOR
    assert "_cancelShapeDraft();" in EDITOR


def test_outer_gallery_escape_bridge_cancels_the_same_transient_tools():
    start = EDITOR.index("window.__galleryEditorHandleEscape = () =>")
    end = EDITOR.index("// ── Lasso tool ──", start)
    bridge = EDITOR[start:end]
    assert "_cancelCrop('escape')" in bridge
    assert "_marqueeTool.cancel('escape')" in bridge
    assert "_cancelLasso()" in bridge
    assert "_gradientTool.cancel()" in bridge
    assert "_cancelShapeDraft()" in bridge


def test_lasso_cancel_clears_partial_polygon_and_refreshes_ui():
    start = LASSO.index("    cancel()")
    block = LASSO[start:LASSO.index("    },", start) + len("    },")]
    assert "state.lassoActive = false" in block
    assert "state.lassoPoints = []" in block
    assert "composite();" in block
    assert "syncToolClearIndicators();" in block


def test_stroke_cancel_restores_history_and_refreshes_the_editor():
    start = STROKE.index("    cancel()")
    block = STROKE[start:STROKE.index("    },", start) + len("    },")]
    assert "state.drawing = false" in block
    assert "undo?.();" in block
    assert "state.redoStack = []" in block
    assert "composite();" in block
    assert "renderLayerPanel();" in block


def test_pointer_and_touch_cancel_use_rollback_path_instead_of_committing():
    assert "if (e.type === 'pointercancel') cancelDraw?.();" in CANVAS_EVENTS
    assert "state.mainCanvas.addEventListener('touchcancel'" in CANVAS_EVENTS
    touch_cancel = CANVAS_EVENTS.index("state.mainCanvas.addEventListener('touchcancel'")
    assert CANVAS_EVENTS.index("cancelDraw?.();", touch_cancel) < CANVAS_EVENTS.index("// Direct pan gestures", touch_cancel)
    assert "if (e?.type === 'pointercancel') cancelDraw?.();" in CANVAS_EVENTS
    assert "if (state.transformActive) _cancelTransform();" in EDITOR
