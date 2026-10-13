from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")


def test_history_snapshot_captures_document_and_layer_state():
    snapshot = EDITOR[EDITOR.index("function _snapshotState()") : EDITOR.index("function _saveState(")]

    assert "activeLayerId: state.activeLayerId" in snapshot
    assert "nextLayerId: state.nextLayerId" in snapshot
    assert "lassoPoints: (state.lassoPoints || []).map" in snapshot
    assert "JSON.stringify(l.adjustments || {})" in snapshot
    assert "offset: { ...(state.layerOffsets.get(l.id)" in snapshot


def test_history_restore_restores_selection_allocator_and_adjustments():
    restore = EDITOR[EDITOR.index("function _restoreState(") : EDITOR.index("function undo()")]

    assert "layer.adjustments = s.adjustments" in restore
    assert "state.nextLayerId = Number.isFinite(snap.nextLayerId)" in restore
    assert "state.activeLayerId = snap.activeLayerId || state.activeLayerId" in restore
    assert "state.lassoPoints = (snap.lassoPoints || []).map" in restore


def test_undo_and_redo_persist_and_keep_action_labels():
    history = EDITOR[EDITOR.index("function undo()") : EDITOR.index("// Jump to any state")]

    assert history.count("_schedulePersist();") == 2
    assert history.count("cur._label = target._label || 'Edit';") == 2
    assert "_restoreState(target);" in history


def test_move_only_creates_history_after_position_changes():
    move = (ROOT / "static/js/editor/tools/move.js").read_text(encoding="utf-8")
    begin = move[move.index("begin(e)") : move.index("drag(e)")]
    drag = move[move.index("drag(e)") : move.index("end()")]

    assert "saveState(" not in begin
    assert "const before = state.layerOffsets.get(layer.id)" in drag
    assert "if (nx === before.x && ny === before.y) return;" in drag
    assert "if (!state.moveHistorySaved)" in drag
    assert "if (!state.transformActive) saveState(" in drag
