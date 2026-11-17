import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/render-cancellation.js").as_uri()
EDITOR_SOURCE = ROOT / "static/js/galleryEditor.js"
ADJUSTMENT_SOURCE = ROOT / "static/js/editor/adjustment-layer.js"
EFFECTS_SOURCE = ROOT / "static/js/editor/effects.js"


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{createRenderGeneration}} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_new_render_generation_invalidates_previous_work():
    result = run_node(
        """
        const generations = createRenderGeneration();
        const first = generations.begin();
        const second = generations.begin();
        console.log(JSON.stringify({first:first.isCurrent(), second:second.isCurrent()}));
        """
    )
    assert result == {"first": False, "second": True}


def test_cancel_only_invalidates_the_cancelled_generation():
    result = run_node(
        """
        const generations = createRenderGeneration();
        const render = generations.begin();
        render.cancel();
        const next = generations.begin();
        console.log(JSON.stringify({cancelled:render.isCurrent(), next:next.isCurrent()}));
        """
    )
    assert result == {"cancelled": False, "next": True}


def test_live_composite_coalesces_worker_generations_to_one_latest_rerender():
    editor = EDITOR_SOURCE.read_text()
    assert "let _asyncCompositeInFlight = null;" in editor
    assert "let _asyncCompositeQueued = false;" in editor
    assert "_renderGeneration.invalidate();" in editor
    assert "_asyncCompositeQueued = true;" in editor
    assert "if (_asyncCompositeQueued)" in editor


def test_editor_close_invalidates_async_composite_lifecycle():
    editor = EDITOR_SOURCE.read_text()
    close_start = editor.index("export function closeEditor(")
    close_source = editor[close_start:]
    assert "_renderGeneration.invalidate();" in close_source
    assert "_asyncCompositeQueued = false;" in close_source
    assert "_asyncCompositeInFlight = null;" in close_source


def test_stale_adjustment_worker_errors_do_not_run_full_resolution_fallback():
    adjustment = ADJUSTMENT_SOURCE.read_text()
    assert "worker.onerror = () => finish(shouldContinue() ? applyAdjustment(source, adjustment) : null);" in adjustment
    assert "return shouldContinue() ? applyAdjustment(source, adjustment) : null;" in adjustment
    assert "if (!shouldContinue()) return false;\n  adjusted = renderWithLayerMasks" in adjustment


def test_stale_effect_worker_errors_do_not_run_full_resolution_fallback():
    effects = EFFECTS_SOURCE.read_text()
    assert "return shouldContinue() ? renderEffects(source, effects, shouldContinue) : null;" in effects
    assert "worker.onerror = () => finish(shouldContinue() ? renderEffects(source, effects, shouldContinue) : null);" in effects
    assert "if (!shouldContinue()) {\n        sourceBitmap.close?.();" in effects


def test_autosave_thumbnail_uses_only_a_completed_composite_surface():
    editor = EDITOR_SOURCE.read_text()
    assert "state.documentRenderReady = false;" in editor
    assert "state.documentRenderReady = true;" in editor
    assert "state.documentRenderReady && state.documentCompositeCanvas" in editor
    assert ": flatten();" in editor


def test_autosave_thumbnail_encoding_can_leave_the_main_thread():
    editor = EDITOR_SOURCE.read_text()
    assert "async function _buildThumbnailAsync()" in editor
    assert "new URL('./editor/thumbnail-worker.js', import.meta.url)" in editor
    assert "const thumbnailPromise = _buildThumbnailAsync();" in editor
    assert "const savePromise = doSave();" in editor
    assert "state.persistInFlight = savePromise" in editor


def test_autosave_serializes_canvas_payloads_from_an_immutable_metadata_snapshot():
    editor = EDITOR_SOURCE.read_text()
    assert "function _serializationSnapshot(source, canvasProxy)" in editor
    assert "new URL('./editor/serialization-worker.js', import.meta.url)" in editor
    assert "const payload = _serializeEditorDocument(snapshot);" in editor
    assert "const encoded = await _encodeSerializedCanvases(canvases);" in editor
    assert "const payloadPromise = _buildDraftPayloadAsync();" in editor


def test_autosave_captures_payload_and_thumbnail_before_close_can_clear_state():
    editor = EDITOR_SOURCE.read_text()
    assert "const payloadPromise = _buildDraftPayloadAsync();" in editor
    assert "const thumbnailPromise = _buildThumbnailAsync();" in editor
    assert "const [payload, thumbnail] = await Promise.all([payloadPromise, thumbnailPromise]);" in editor
