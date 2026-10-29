from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_state_declares_a_history_snapshots_list():
    state = _read("static/js/editor/state.js")
    assert "historyPanelEl: null," in state
    assert "historySnapshots: []," in state


def test_gallery_editor_defines_take_restore_delete_and_list_snapshot_helpers():
    ge = _read("static/js/galleryEditor.js")
    assert "let _historySnapshotSeq = 0;" in ge
    assert "function _takeHistorySnapshot(label) {" in ge
    assert "function _restoreHistorySnapshot(id) {" in ge
    assert "function _deleteHistorySnapshot(id) {" in ge
    assert "function _listHistorySnapshots() {" in ge
    # Independent labelled snapshots, capped, keyed with a stable id.
    assert "snap._snapshotId = 'snap-' + (++_historySnapshotSeq);" in ge
    assert "while (state.historySnapshots.length > 20) state.historySnapshots.shift();" in ge
    # Restore pushes the pre-restore state so it is itself undoable, then
    # restores the snapshot through the existing state restorer.
    assert "cur._label = 'Before snapshot: ' + (snap._label || 'Snapshot');" in ge
    assert "_restoreState(snap);" in ge
    assert "if (!snap) throw new Error('snapshot not found');" in ge


def test_history_panel_receives_the_snapshot_callbacks():
    ge = _read("static/js/galleryEditor.js")
    for dep in ("takeSnapshot:", "restoreSnapshot:", "deleteSnapshot:", "listSnapshots:"):
        assert dep in ge, dep


def test_control_ops_expose_snapshots():
    ge = _read("static/js/galleryEditor.js")
    assert "'history.snapshot': { description: 'Create a named snapshot {label}" in ge
    assert "'history.snapshots': { description: 'List the named snapshots" in ge
    assert "'history.restoreSnapshot': { description: 'Restore a named snapshot {id}." in ge
    assert "'history.deleteSnapshot': { description: 'Delete a named snapshot {id}." in ge
    assert "run: (p = {}) => _takeHistorySnapshot(p && p.label)" in ge
    assert "return _restoreHistorySnapshot(p.id);" in ge
    assert "return _deleteHistorySnapshot(p.id);" in ge


def test_history_panel_renders_a_snapshots_section():
    panel = _read("static/js/editor/history-panel.js")
    assert "export function createHistoryPanel({ undo, redo, takeSnapshot, restoreSnapshot, deleteSnapshot, listSnapshots }) {" in panel
    assert "function renderSnapshots(container) {" in panel
    # A camera "new snapshot" button prompts for a name, and each row restores
    # or deletes its snapshot.
    assert "container.querySelector('.ge-history-snap-new')?.addEventListener('click'" in panel
    assert "const label = window.prompt('Snapshot name', 'Snapshot ' + n);" in panel
    assert "container.querySelectorAll('.ge-history-snap-restore').forEach" in panel
    assert "container.querySelectorAll('.ge-history-snap-del').forEach" in panel
    assert "renderSnapshots, jumpToHistory };" in panel
    # Both the floating panel and the docked History list get a snapshots block.
    assert "renderSnapshots(state.historyPanelEl.querySelector('#ge-history-snapshots'));" in panel
    assert "dockSnaps.id = 'ge-dock-history-snapshots';" in panel


def test_popup_markup_has_a_snapshots_mount_point():
    popups = _read("static/js/editor/build/popups.js")
    assert '<div class="ge-history-snapshots" id="ge-history-snapshots"></div>' in popups


def test_css_styles_the_snapshots_section():
    css = _read("static/css/13-image-editor.css")
    assert ".ge-history-snap-head {" in css
    assert ".ge-history-snap-new {" in css
    assert ".ge-history-snap-rows {" in css
    assert ".ge-history-snap-restore {" in css
    assert ".ge-history-snap-del {" in css
    # The camera button's inline SVG must carry an explicit scoped size — without
    # it the glyph collapses to a zero-width box and renders blank.
    assert ".ge-history-snap-new svg {" in css
