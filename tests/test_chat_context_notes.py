"""Notes in a chat's context: which notes a turn carries, and the record of them on its user message.

A turn carries the notes selected for it (Notes -> Select -> Send to agent),
and in a chat started from a note, that note on every turn. The user message
keeps their ids and titles as sent (`metadata.context_notes`) so the chat can
show them later; the sessions list names the note a chat was started from.
"""
import json
from types import SimpleNamespace

import pytest

import core.database as cdb
import core.note_agent_scope as scope_mod
import routes.chat_routes as chat_routes
from core.database import Note, Session as DbSession
from routes.chat_helpers import PreprocessedMessage, add_user_message
from src.tools import notes as notes_tool
from tests.helpers.database import disposable_database

MINE = "mine0001-note-aaaa"
OTHER = "othr0002-note-bbbb"
THEIRS = "thrs0003-note-cccc"
SID = "chat-from-note"
TODO_SID = "chat-from-a-todo"
PLAIN = "an-ordinary-chat"


@pytest.fixture
def db(monkeypatch, tmp_path):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(notes_tool, "_SEEN_VERSIONS", type(notes_tool._SEEN_VERSIONS)())
        monkeypatch.setattr(scope_mod, "_SEEN_BY_SESSION", type(scope_mod._SEEN_BY_SESSION)())
        with factory() as s:
            s.add(Note(id=MINE, owner="alice", title="Trip", content="Pack light\n- [ ] book the cabin"))
            s.add(Note(id=OTHER, owner="alice", title="Groceries", content="- [ ] milk"))
            s.add(Note(id=THEIRS, owner="bob", title="Bob's", content="secret"))
            s.add(DbSession(id=SID, name="Agent: Trip", endpoint_url="", model="m", owner="alice", note_scope_id=MINE))
            s.add(DbSession(id=TODO_SID, name="Agent: book the cabin", endpoint_url="", model="m", owner="alice",
                            note_scope_id=MINE, note_focus_task="book the cabin"))
            s.add(DbSession(id=PLAIN, name="Plain", endpoint_url="", model="m", owner="alice"))
            s.commit()
        yield factory


async def _turn(monkeypatch, db, session_id, selected=None):
    """One chat_stream turn as alice; returns what the context builder was given."""
    from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured, capture_context=True)

    async def agent_stream(*args, **kwargs):
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", agent_stream)
    monkeypatch.setattr(chat_routes, "SessionLocal", db)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *a, **kw: ("edit it", session_id))
    request = _RouteRequest("agent")
    request._form.update({"compare_mode": "false", "session": session_id, "message": "edit it"})
    if selected is not None:
        request._form["selected_note_ids"] = json.dumps(selected)
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass
    return captured["build_context"]


@pytest.mark.asyncio
async def test_a_follow_up_in_a_note_chat_carries_its_note(monkeypatch, db):
    built = await _turn(monkeypatch, db, SID)
    assert built["context_notes"] == [{"id": MINE, "title": "Trip"}]
    assert "Pack light" in built["selected_notes_context"]


@pytest.mark.asyncio
async def test_a_chat_started_from_a_todo_records_the_todo_with_its_note(monkeypatch, db):
    # The chat shows the todo on the message, since the prompt does not name it.
    built = await _turn(monkeypatch, db, TODO_SID, selected=[OTHER])
    assert built["context_notes"] == [
        {"id": MINE, "title": "Trip", "focus_task": "book the cabin"},
        {"id": OTHER, "title": "Groceries"},
    ]
    assert "Focus todo: book the cabin" in built["selected_notes_context"]


@pytest.mark.asyncio
async def test_the_chats_note_comes_first_and_is_not_repeated(monkeypatch, db):
    built = await _turn(monkeypatch, db, SID, selected=[OTHER, MINE])
    assert built["context_notes"] == [{"id": MINE, "title": "Trip"}, {"id": OTHER, "title": "Groceries"}]
    assert built["selected_notes_context"].count("Pack light") == 1


@pytest.mark.asyncio
async def test_selected_notes_keep_their_order_and_only_mine_count(monkeypatch, db):
    built = await _turn(monkeypatch, db, PLAIN, selected=[OTHER, THEIRS, MINE])
    assert built["context_notes"] == [{"id": OTHER, "title": "Groceries"}, {"id": MINE, "title": "Trip"}]
    assert "secret" not in built["selected_notes_context"]


@pytest.mark.asyncio
async def test_an_ordinary_chat_with_nothing_selected_carries_no_note(monkeypatch, db):
    built = await _turn(monkeypatch, db, PLAIN)
    assert built["context_notes"] == []
    assert not built["selected_notes_context"]


def _preprocessed(text="hi"):
    return PreprocessedMessage(enhanced_message=text, user_content=text, text_for_context=text, youtube_transcripts=[], attachment_meta=[])


def test_the_user_message_records_its_context_notes():
    added = []
    sess = SimpleNamespace(add_message=added.append)
    handler = SimpleNamespace(update_session_name_if_needed=lambda *a: None)
    notes = [{"id": MINE, "title": "Trip"}]
    add_user_message(sess, handler, _preprocessed(), interaction_mode="agent", context_notes=notes)
    assert added[0].metadata["context_notes"] == notes
    added.clear()
    add_user_message(sess, handler, _preprocessed(), interaction_mode="agent")
    assert "context_notes" not in (added[0].metadata or {})


def test_the_sessions_list_names_a_chats_note(monkeypatch, db):
    from unittest.mock import MagicMock
    import routes.session_routes as session_routes
    monkeypatch.setattr(session_routes, "SessionLocal", db)
    monkeypatch.setattr(session_routes, "effective_user", lambda request: "alice")
    manager = MagicMock()
    # Both chats are live in memory, so the list keeps them though they have no messages.
    manager.get_sessions_for_user.return_value = {SID: MagicMock(), PLAIN: MagicMock()}
    router = session_routes.setup_session_routes(manager, {})
    endpoint = [r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/sessions" and "GET" in getattr(r, "methods", set())][-1]
    listed = {row["id"]: row for row in endpoint(request=MagicMock())}
    assert listed[SID]["note_scope_id"] == MINE
    assert listed[PLAIN]["note_scope_id"] is None
