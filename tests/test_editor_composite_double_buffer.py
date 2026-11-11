from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")


def test_async_composite_renders_into_a_back_buffer():
    assert "let _documentBackCanvas = null;" in EDITOR
    assert "const copyBack = () => {" in EDITOR
    start = EDITOR.index("  if (asyncComposite) {")
    end = EDITOR.index("    _asyncCompositeInFlight = job;", start)
    block = EDITOR[start:end]
    assert "_documentBackCanvas = document.createElement('canvas');" in block
    assert "_drawDocumentLayersAsync(back.getContext('2d'), render.isCurrent)" in block
    assert "_drawDocumentLayers(back.getContext('2d'), render.isCurrent);" in block
    # The document canvas must only be written by a completed generation, so an
    # invalidated render can never leave a half-drawn document on screen.
    assert "_drawDocumentLayersAsync(documentCanvas.getContext('2d')" not in block
    assert "_drawDocumentLayers(documentCanvas.getContext('2d')" not in block
    assert block.count("if (!render.isCurrent()) return;") >= 2


def test_completed_generation_replaces_the_document_atomically():
    copy = EDITOR[EDITOR.index("    const copyBack = () => {"):EDITOR.index("    const renderPromise =")]
    assert "dctx.clearRect(0, 0, documentCanvas.width, documentCanvas.height);" in copy
    assert "dctx.drawImage(back, 0, 0);" in copy
    assert "_finishComposite(render, documentCanvas);" in copy


def test_direct_drawing_stays_synchronous_for_live_feedback():
    start = EDITOR.index("  const asyncComposite = _hasRetainedEffects()")
    end = EDITOR.index(";", EDITOR.index("typeof OffscreenCanvas !== 'undefined'", start))
    gate = EDITOR[start:end]
    assert "&& !state.drawing" in gate
    assert "&& !state.moving" in gate
