from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "group.js"
).read_text(encoding="utf-8")

SESSIONS_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "sessions.js"
).read_text(encoding="utf-8")


def test_group_session_sidebar_cache_uses_safe_json_loader():
    assert "import Storage from './storage.js';" in SOURCE
    assert "Storage.getJSON('odysseus-group-sessions', [])" in SOURCE
    assert "Array.isArray(storedGroupSessions)" in SOURCE
    assert "JSON.parse(localStorage.getItem('odysseus-group-sessions')" not in SOURCE


def test_group_state_persists_to_server_and_restores_async():
    assert "async function _saveStateToServer()" in SOURCE
    assert "/group_state" in SOURCE
    assert "export async function restoreState(sessionId)" in SOURCE
    assert "await window.groupModule.restoreState(id)" in SESSIONS_SOURCE


def test_group_participants_render_as_non_session_sidebar_rows():
    assert "GROUP_PARTICIPANTS_EXPANDED_KEY" in SESSIONS_SOURCE
    assert "group-participant-toggle" in SESSIONS_SOURCE
    assert "group-participant-row" in SESSIONS_SOURCE
    assert "row.setAttribute('aria-label'" in SESSIONS_SOURCE
    assert "row.setAttribute('data-session-id'" not in SESSIONS_SOURCE
