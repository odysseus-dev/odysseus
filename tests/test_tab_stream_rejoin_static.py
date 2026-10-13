from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "static/js/chat.js").read_text()


def _visibility_handler() -> str:
    start = CHAT.index("document.addEventListener('visibilitychange', () => {")
    end = CHAT.index("// On mobile, fade out welcome text", start)
    return CHAT[start:end]


def test_tab_return_never_aborts_the_detached_run():
    handler = _visibility_handler()
    assert "_probeStaleLocalStream()" in handler
    assert ".abort()" not in handler
    assert "_activeStreams.delete" not in handler
    assert "updateSubmitButton('idle'" not in handler


def test_backend_authoritative_replay_path_remains_enabled():
    assert "export async function resumeStream(sessionId" in CHAT
    assert "/api/chat/resume/${sessionId}" in CHAT


def test_local_background_marker_does_not_block_server_rejoin():
    active_check = CHAT.split("function hasActiveStream", 1)[1].split("function _getForegroundStreamState", 1)[0]
    detach = CHAT.split("export function detachCurrentStream", 1)[1].split(
        "export async function resumeStream", 1
    )[0]
    background = CHAT.split("export function checkBackgroundStream", 1)[1].split(
        "function _markCompactPre", 1
    )[0]

    assert "_backgroundStreams.has(sessionId)" not in active_check
    assert "active.abortCtrl._reason = 'detach'" in detach
    assert "_activeStreams.delete(sessionId)" in detach
    assert "active.abortCtrl.abort()" in detach
    assert "Response streaming in background" not in background


def test_rejoined_stream_restores_stop_control_and_structured_events():
    resume = CHAT.split("export async function resumeStream", 1)[1].split(
        "export function checkBackgroundStream", 1
    )[0]

    assert "updateSubmitButton('streaming', submitBtn)" in resume
    assert "startReplayTool(json)" in resume
    assert "finishReplayTool(json)" in resume
    assert "startReplayRound()" in resume
    assert "markdownModule.processWithThinking" in resume
