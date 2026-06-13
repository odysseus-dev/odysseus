from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "group.js"
).read_text(encoding="utf-8")

SESSIONS_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "sessions.js"
).read_text(encoding="utf-8")

APP_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "app.js"
).read_text(encoding="utf-8")

RENDERER_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "chatRenderer.js"
).read_text(encoding="utf-8")

CHAT_ROUTES_SOURCE = (
    Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"
).read_text(encoding="utf-8")

MODEL_PICKER_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "modelPicker.js"
).read_text(encoding="utf-8")

CHAT_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "chat.js"
).read_text(encoding="utf-8")

INDEX_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "index.html"
).read_text(encoding="utf-8")

STYLE_SOURCE = (
    Path(__file__).resolve().parent.parent / "static" / "style.css"
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


def test_group_participant_rows_open_raw_child_sessions():
    assert "row.dataset.groupParticipantId" in SESSIONS_SOURCE
    assert "Open raw chat for" in SESSIONS_SOURCE
    assert "await selectSession(participant.id, { keepSidebar: true })" in SESSIONS_SOURCE
    assert "window.groupModule.setWhisperTarget(participant.id)" not in SESSIONS_SOURCE
    assert "window.groupModule.clearWhisperTarget()" in SESSIONS_SOURCE
    assert "_groupChildSessions" in SESSIONS_SOURCE
    assert "_currentSessionDetails" in SESSIONS_SOURCE
    assert "_groupChildSessions.has(String(hashId))" in SESSIONS_SOURCE


def test_group_child_raw_chat_keeps_whisper_context_without_model_picker():
    assert 'id="whisper-toggle-btn"' in INDEX_SOURCE
    assert "#whisper-toggle-btn.active" in STYLE_SOURCE
    assert "window._syncWhisperIndicator = _syncWhisperIndicator" in APP_SOURCE
    assert "sessionModule.getCurrentGroupChildInfo" in APP_SOURCE
    assert "Send whisper to" in APP_SOURCE
    assert "export function getCurrentGroupChildInfo()" in SESSIONS_SOURCE
    assert "export function isCurrentGroupChild()" in SESSIONS_SOURCE
    assert "export function clearPendingChat()" in SESSIONS_SOURCE
    assert "if (groupChildInfo) {\n      _pendingChat = null;" in SESSIONS_SOURCE
    assert "window._syncWhisperIndicator(true, groupChildInfo)" in SESSIONS_SOURCE
    assert "isCurrentGroupChild," in SESSIONS_SOURCE
    assert "deps.isCurrentGroupChild" in MODEL_PICKER_SOURCE
    assert "sessionModule.clearPendingChat" in CHAT_SOURCE


def test_group_child_raw_chat_uses_participant_alias_for_assistant_labels():
    assert "childMeta.character_name = childMeta.character_name || groupChildInfo.name" in SESSIONS_SOURCE
    assert "const groupChildAlias = groupChildInfo && groupChildInfo.name" in CHAT_SOURCE
    assert "holder._characterName = _charNameInit || ''" in CHAT_SOURCE
    assert "json.character_name || holder._characterName" in CHAT_SOURCE
    assert "if (metadata?.character_name) roleEl.textContent = metadata.character_name" in RENDERER_SOURCE


def test_group_internal_sends_do_not_trigger_child_mirroring():
    assert "let _whisperTargetSessionId = null;" in SOURCE
    assert "export function setWhisperTarget(sessionId)" in SOURCE
    assert "export function getWhisperUserMetadata()" in SOURCE
    assert "async function _sendWhisper(msg, box, target)" in SOURCE
    assert "_streamToHolder(target.index, target.sessionId, msg, holder, ac, { whisper: true })" in SOURCE
    whisper_block = SOURCE.split("async function _sendWhisper", 1)[1].split("async function _sendParallel", 1)[0]
    assert "_syncAllResponses" not in whisper_block
    assert "Private whisper from the user" not in SOURCE
    assert "This is a private direct message from the user to you" in SOURCE
    assert "fd.append('group_internal', 'true')" in SOURCE
    assert 'group_internal = str(form_data.get("group_internal", "")' in CHAT_ROUTES_SOURCE
    assert "_group_child_whisper_context(session, _user)" in CHAT_ROUTES_SOURCE
    assert "_mirror_group_child_user_message" in CHAT_ROUTES_SOURCE
    assert "_mirror_group_child_assistant_message" in CHAT_ROUTES_SOURCE
    assert "group_whisper: true" in SOURCE
    assert "whisper_to" in SOURCE
    assert "whisper_from" in SOURCE


def test_group_submit_and_history_render_whisper_metadata():
    assert "groupModule.getWhisperUserMetadata" in APP_SOURCE
    assert "chatRenderer.addMessage('user', msg, null, userMetadata)" in APP_SOURCE
    assert "metadata?.group_whisper" in RENDERER_SOURCE
    assert "'Whisper to ' + metadata.whisper_to" in RENDERER_SOURCE
    assert "'Whisper from ' + metadata.whisper_from" in RENDERER_SOURCE
