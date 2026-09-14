from pathlib import Path


APP_JS = Path("static/app.js")
CHAT_JS = Path("static/js/chat.js")
SESSIONS_JS = Path("static/js/sessions.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_fresh_chat_clears_restore_and_active_session_state():
    app = APP_JS.read_text(encoding="utf-8")
    sessions = SESSIONS_JS.read_text(encoding="utf-8")

    fresh_chat = _slice(app, "function _startFreshChat()", "/** Sync Research indicator")
    deselect_current = _slice(sessions, "function _deselectCurrentSession(sid)", "// Reset send button to idle state")
    set_current_session = _slice(sessions, "export function setCurrentSessionId(id)", "export function getSortMode()")

    assert "sessionModule.setCurrentSessionId(null)" in fresh_chat
    assert "window.__odysseusLastSelectedSessionId = id || ''" in set_current_session
    assert "window.__odysseusLastSelectedSessionId = ''" in deselect_current
    assert "Storage.remove('lastSessionId')" in set_current_session
    assert "Storage.remove('lastSessionId')" in deselect_current
    assert ".list-item.active-session, .session-item.active" in set_current_session
    assert ".list-item.active-session, .session-item.active" in deselect_current


def test_first_send_in_new_chat_posts_materialized_session_id():
    chat = CHAT_JS.read_text(encoding="utf-8")
    send_path = _slice(chat, "export async function handleChatSubmit(e)", "export function abortCurrentRequest")

    materialize_positions = [
        idx
        for idx in range(len(send_path))
        if send_path.startswith("sessionModule.materializePendingSession()", idx)
    ]
    stream_id_pos = send_path.index("const streamSessionId = sessionModule.getCurrentSessionId();")
    post_session_pos = send_path.index("fd.append('session', streamSessionId);")

    assert materialize_positions
    assert max(materialize_positions) < stream_id_pos < post_session_pos
