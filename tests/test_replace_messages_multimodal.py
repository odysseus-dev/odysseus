"""replace_messages must persist readable, path-free multimodal history.

Live model input may contain provider-specific media blocks and inline data
URLs. Compaction uses replace_messages for the retained transcript, which must
store readable text plus stable structured attachment references without
copying raw base64 payloads into ChatMessage.content.
"""
import uuid

import pytest

import core.database as cdb
from core.models import ChatMessage
from tests.helpers.sqlite_db import make_temp_sqlite

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture
def manager(monkeypatch):
    import core.session_manager as sm
    monkeypatch.setattr(sm, "SessionLocal", _TS)
    mgr = sm.SessionManager.__new__(sm.SessionManager)
    mgr.sessions = {}
    mgr.upload_handler = None
    return mgr


def _make_session(sid, owner="alice"):
    db = _TS()
    try:
        db.add(cdb.Session(id=sid, owner=owner, name="chat", model="gpt-4o",
                           endpoint_url="http://localhost:11434",
                           archived=False, message_count=1))
        db.commit()
    finally:
        db.close()


def test_multimodal_content_persists_text_and_attachment_ref_without_payload(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)

    upload_id = "a" * 32 + ".png"
    multimodal = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    msgs = [ChatMessage(
        role="user",
        content=multimodal,
        metadata={
            "attachments": [{
                "id": upload_id,
                "name": "diagram.png",
                "mime": "image/png",
                "size": 4,
                "checksum_sha256": "sha256-digest",
            }]
        },
    )]
    assert manager.replace_messages(sid, msgs) is True

    expected = (
        "what is this?\n"
        "[1 inline media payload omitted]\n"
        f"[Attachment: diagram.png | id={upload_id} | mime=image/png | "
        "size=4 bytes | sha256=sha256-digest]"
    )

    db = _TS()
    try:
        stored = db.query(cdb.ChatMessage).filter_by(session_id=sid).one()
        assert stored.content == expected
        assert "data:image/png;base64,AAAA" not in stored.content
        assert "base64" not in stored.content
        assert "AAAA" not in stored.content
    finally:
        db.close()

    # Drop the in-memory cache so the next read hydrates from the DB.
    manager.sessions.clear()
    reloaded = manager.get_session(sid)
    assert len(reloaded.history) == 1
    persisted = reloaded.history[0].content
    assert isinstance(persisted, str)
    assert persisted == expected
    assert reloaded.history[0].metadata["attachments"][0]["id"] == upload_id
    assert (
        reloaded.history[0].metadata["attachments"][0]["checksum_sha256"]
        == "sha256-digest"
    )


def test_jsonlike_plain_string_content_still_round_trips(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)
    text = '[{"type": "object", "name": "foo"}]'
    msgs = [ChatMessage(role="user", content=text)]
    assert manager.replace_messages(sid, msgs) is True
    manager.sessions.clear()
    reloaded = manager.get_session(sid)
    assert isinstance(reloaded.history[0].content, str)
    assert reloaded.history[0].content == text


def test_replace_messages_keeps_history_alias_for_context_messages(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)
    msgs = [ChatMessage(role="user", content="original")]
    assert manager.replace_messages(sid, msgs) is True

    session = manager.sessions[sid]
    assert session.history is session._history

    session.history.append(ChatMessage(role="user", content="after direct mutation"))
    assert session.get_context_messages()[-1]["content"] == "after direct mutation"
