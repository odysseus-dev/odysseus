from pathlib import Path


APP_JS = Path("static/app.js")
SESSIONS_JS = Path("static/js/sessions.js")


def test_rail_delete_uses_hard_delete_endpoint():
    source = APP_JS.read_text()
    rail_block = source[source.index("const railDelete = el('rail-delete-session');"):]
    rail_block = rail_block[:rail_block.index("// Textarea auto-resize")]

    assert "fetch(`${API_BASE}/api/session/${currentId}`, { method: 'DELETE' })" in rail_block
    assert "api/session/${currentId}/archive" not in rail_block


def test_deleted_sessions_are_pruned_from_local_sidebar_state():
    source = SESSIONS_JS.read_text()

    assert "function _removeSessionFromLocalState(sid)" in source
    assert "sessions = sessions.filter(s => String(s.id) !== id);" in source
    assert "Storage.set('session-order', JSON.stringify(orderIds.filter(x => String(x) !== id)))" in source
    assert "_removeSessionFromLocalState(s.id);" in source


def test_session_fetch_normalizes_duplicate_ids_before_render():
    source = SESSIONS_JS.read_text()

    assert "function _normalizeSessionsList(fetched)" in source
    assert "if (seen.has(id)) continue;" in source
    assert "sessions = _normalizeSessionsList(fetched);" in source
