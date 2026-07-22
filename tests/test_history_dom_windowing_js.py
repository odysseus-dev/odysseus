"""Cross-module guards for bounded history rendering and safe navigation."""

from pathlib import Path


SESSIONS_SRC = Path(__file__).resolve().parent.parent / "static/js/sessions.js"


def _source() -> str:
    return SESSIONS_SRC.read_text(encoding="utf-8")


def test_session_switch_checks_detach_admission_before_navigation_mutation():
    text = _source()
    start = text.index("export async function selectSession(")
    body = text[start : text.index("// Pending session", start)]

    admission = body.index("detachCurrentStream(prevSessionId)")
    nav_token = body.index("const navToken = ++_sessionNavToken")
    current_session = body.index("currentSessionId = id")
    assert admission < nav_token < current_session
    assert "return false;" in body[admission : nav_token]


def test_new_chat_checks_detach_admission_before_mutating_state():
    text = _source()
    start = text.index("export function createDirectChat(")
    body = text[start : start + 1800]

    admission = body.index("detachCurrentStream(currentSessionId)")
    nav_token = body.index("_sessionNavToken++")
    pending = body.index("_pendingChat =")
    assert admission < nav_token < pending
    assert "return false;" in body[admission : nav_token]


def test_loading_older_pages_trims_from_newest_edge():
    text = _source()
    start = text.index("const loadOlder = async () =>")
    body = text[start : text.index("_historyPager.handler", start)]
    insert = body.index("box.insertBefore")
    trim = body.index("trimChatHistoryDOM({ from: 'end' })")
    assert insert < trim


def test_archived_peek_uses_real_page_limit_and_installs_pager():
    text = _source()
    start = text.index("async function _arcPeekOpen(")
    body = text[start : text.index("// When navigating away", start)]
    assert "limit: _historyPageLimit()" in body
    assert "?limit=400" not in body
    assert "_installHistoryPager(sid" in body


def test_history_renderer_tags_all_nodes_from_one_message_group():
    text = _source()
    assert "node.dataset.domTrimGroup = activeTrimGroup" in text
    assert "_activeHistoryTurnGroup = `history-turn-" in text
    assert "node.classList.contains('msg-continuation')" in text
    assert "activeTrimGroup = `${trimGroup}-round-" in text
