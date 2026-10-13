from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "static/js/chat.js").read_text()
SESSIONS = (ROOT / "static/js/sessions.js").read_text()
CSS = app_css()


def test_queued_prompts_are_persisted_per_session_and_restored_on_return():
    assert "odysseus-queued-agent-requests-v1" in CHAT
    assert "function _persistQueuedRequests()" in CHAT
    assert "function _restoreQueuedRequestsForCurrentSession()" in CHAT
    assert "_restoreQueuedRequestsForCurrentSession();" in CHAT
    assert "_drainQueuedAgentRequests();" in CHAT


def test_background_completion_survives_rerender_and_hidden_selected_chat():
    assert "odysseus-completed-chat-sessions-v1" in SESSIONS
    assert "_persistCompletedSessions();" in SESSIONS
    assert "document.visibilityState === 'visible'" in SESSIONS
    assert "markStreamComplete(streamSessionId, { force: true })" in CHAT
    assert "active.wasAway = true" in CHAT


def test_sidebar_has_clear_working_and_done_states():
    # The provider star carries the run state: it spins while working and
    # becomes a check mark when done. The separate text pill is retired, and
    # any pill left from an older render is removed.
    assert "star.classList.toggle('processing', isRunning)" in SESSIONS
    assert "star.classList.toggle('notify', isCompleted)" in SESSIONS
    assert "listItem.querySelector('.session-run-state')" in SESSIONS
    assert "state.remove();" in SESSIONS
    assert ".session-star.notify::after" in CSS
    assert "polyline points='20 6 9 17 4 12'" in CSS
    assert ".session-star.notify {\n      animation: none;" in CSS
    assert "state.textContent = 'Working'" not in SESSIONS


def test_every_registered_stream_marks_sidebar_working_immediately():
    registration = CHAT[CHAT.index("_activeStreams.set(streamSessionId") : CHAT.index("_syncForegroundStreamGlobals();", CHAT.index("_activeStreams.set(streamSessionId"))]
    assert "sessionModule.markStreaming(streamSessionId)" in registration
    assert ".session-star.provider-logo.processing svg" in CSS
    assert ".session-star.provider-logo.processing svg {\n      animation: none;" in CSS


def test_selected_chat_that_finished_while_away_notifies():
    done = CHAT[CHAT.index("const _completedWhileAway") : CHAT.index("// Force-close thinking", CHAT.index("const _completedWhileAway"))]
    assert "markStreamComplete(streamSessionId, { force: true })" in done
    assert "_notifyStreamComplete(streamSessionId, streamQuery)" in done
