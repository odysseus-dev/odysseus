from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _editor():
    return (ROOT / "static/js/galleryEditor.js").read_text()


def _slice(source, start, end):
    return source[source.index(start):source.index(end)]


def test_every_edit_schedules_a_debounced_autosave():
    source = _editor()
    # state mutations funnel through _saveState, which schedules a persist.
    save_state = _slice(source, "function _saveState", "function closeEditor")
    assert "_schedulePersist()" in save_state

    schedule = _slice(source, "function _schedulePersist", "function _buildDraftPayload")
    assert "const PERSIST_DEBOUNCE_MS = 800;" in source
    assert "if (_draftSavingDisabled()) return;" in schedule
    assert "if (!state.editorOpen || !state.layers.length) return;" in schedule
    # The first save (no server draft yet) establishes the recovery anchor
    # immediately; later edits stay debounced.
    assert "const delay = state.draftId ? PERSIST_DEBOUNCE_MS : 0;" in schedule
    assert "state.persistTimer = setTimeout(() => { state.persistTimer = null; _persistDraft(); }, delay);" in schedule


def test_autosave_creates_then_updates_the_server_draft_and_coalesces():
    persist = _slice(_editor(), "async function _persistDraft", "async function _loadDraftById")

    # Concurrent saves coalesce instead of racing.
    assert "if (state.persistInFlight) { state.persistDirty = true; _setDraftStatus('Saving…', 'saving'); return; }" in persist
    assert "if (isCurrentSession()) state.persistInFlight = null;" in persist
    assert "if (state.persistDirty) {" in persist

    # Create on the first save, update thereafter, and recreate if the row
    # was deleted (404) while the editor was open.
    assert "return fetch('/api/editor-drafts', {" in persist
    assert "method: 'PUT', credentials: 'same-origin'," in persist
    assert "if (res.status === 404) {" in persist
    assert "method: 'POST', credentials: 'same-origin'," in persist

    # Oversized layered documents pause autosave instead of silently failing.
    assert "if (serializedBody.length > _EDITOR_PROJECT_MAX_BYTES) {" in persist
    assert "_setDraftStatus('Save paused', 'error');" in persist


def test_autosave_writes_a_session_recovery_anchor():
    source = _editor()
    write = _slice(source, "function _writeActiveEditorSession", "function _setDraftStatus")
    assert "if (!state.editorOpen) return;" in write
    assert "sessionStorage.setItem(ACTIVE_EDITOR_SESSION_KEY" in write
    assert "draftId: state.draftId || null," in write
    assert "draftName: state.draftName || 'Untitled'," in write
    assert "width: Number(state.imgWidth) || 0," in write
    assert "height: Number(state.imgHeight) || 0," in write
    # The anchor is refreshed once a fresh draft id comes back from the server.
    assert "state.draftId = out.id;\n        _writeActiveEditorSession();" in source


def test_closing_the_editor_flushes_and_clears_the_recovery_anchor():
    source = _editor()
    close = source[source.index("export function closeEditor"):]
    assert "if (state.persistTimer) { clearTimeout(state.persistTimer); state.persistTimer = null; }" in close
    assert "try { _persistDraft(); } catch {}" in close
    assert "state.editorSessionToken += 1;" in close
    assert "_clearActiveEditorSession();" in close


def test_open_editor_resumes_a_draft_by_server_id_with_a_liveness_guard():
    source = _editor()
    open_fn = source[source.index("export function openEditor"):]
    assert "if (!imageUrl && draftId) {" in open_fn
    assert "return _loadDraftById(draftId)" in open_fn
    # A late draft load must not draw into a torn-down editor.
    assert "if (!state.editorOpen) return;" in open_fn


def test_gallery_restores_the_active_edit_after_refresh():
    gallery = (ROOT / "static/js/gallery.js").read_text()
    restore = _slice(gallery, "async function _restoreActiveEditorAfterRefresh", "if (document.readyState")
    assert "const saved = _readActiveEditorSession();" in restore
    assert "if (!saved) return;" in restore
    # It reopens the same draft (or source image) at the same canvas size.
    assert "saved.draftId ? null : (saved.imageUrl || null)," in restore
    assert "saved.draftId || null," in restore
    assert "saved.width > 0 && saved.height > 0 ? { w: saved.width, h: saved.height } : null," in restore
    # Wiring: run the restore once the shell has mounted after a reload.
    assert "document.addEventListener('DOMContentLoaded', () => setTimeout(_restoreActiveEditorAfterRefresh, 0), { once: true });" in gallery
