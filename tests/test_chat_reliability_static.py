"""Frontend/route wiring of the chat reliability behaviour (static: the client logic lives in a large module that cannot run without a browser)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / 'static/js/chat.js').read_text(encoding='utf-8')
ROUTES = (ROOT / 'routes/chat_routes.py').read_text(encoding='utf-8')


def test_replay_gap_is_handled_by_the_live_stream_and_by_a_resumed_stream():
    assert CHAT.count("json.type === 'replay_gap'") == 2
    resume = CHAT[CHAT.index('export async function resumeStream'):]
    gap = resume[resume.index("json.type === 'replay_gap'"):][:160]
    assert 'rich = true' in gap and '_noteReplayGap' in gap                 # a resumed run is then rebuilt from the saved conversation, not shown as complete
    assert 'function _noteReplayGap' in CHAT and 'replay-gap-note' in CHAT


def test_stop_shows_stopping_then_stopped_only_after_the_server_confirms():
    body = CHAT[CHAT.index('function _postExactStop'):][:2600]
    assert "_showStopState('stopping')" in body and "_showStopState('stopped', body)" in body and 'body.confirmed' in body
    assert "method: 'POST'" in body and "`/api/chat/stop/${encodeURIComponent(sessionId)}`" in body and 'state.confirmed' in body                     # polls the run state until it has really ended
    assert 'tools_may_still_run' in CHAT and 'interrupted' in CHAT


def test_the_stop_state_route_is_read_only_and_owner_checked():
    block = ROUTES[ROUTES.index('async def chat_stop_state'):][:500]
    assert '_verify_session_owner(request, session_id)' in block and 'agent_runs.run_state' in block
    assert re.search(r'@router\.get\("/api/chat/stop/\{session_id\}"\)', ROUTES)


def test_chat_js_never_calls_a_toast_helper_it_did_not_import():
    """A bare ``showToast(`` threw a ReferenceError inside Stop before its request was sent (found in the browser run); chat.js reaches it as uiModule.showToast."""
    assert not re.findall(r'(?<![\w.])showToast\(', CHAT)
