"""replace_messages must preserve attachment references without inline bytes.

Compaction must store readable text and stable structured attachment references
without copying raw base64 payloads into ChatMessage.content. That structured
metadata must also survive reload so an owner-checked manifest can be rebuilt.
"""
import io
import uuid
from types import SimpleNamespace

import pytest

import core.database as cdb
from core.models import ChatMessage
from routes.chat_helpers import build_uploaded_file_manifest
from src.attachment_refs import attachment_ids_from_messages
from src.upload_handler import UploadHandler
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


def test_multimodal_content_reloads_and_rebuilds_owner_checked_manifest(
    manager,
    monkeypatch,
    tmp_path,
):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)

    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    alice_upload = handler.save_upload(
        SimpleNamespace(filename="alice.png", file=io.BytesIO(b"alice-image")),
        "127.0.0.1",
        owner="alice",
    )
    bob_upload = handler.save_upload(
        SimpleNamespace(filename="bob.png", file=io.BytesIO(b"bob-image")),
        "127.0.0.2",
        owner="bob",
    )

    multimodal = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    attachment_metadata = [
        {
            "id": alice_upload["id"],
            "name": alice_upload["name"],
            "mime": alice_upload["mime"],
            "size": alice_upload["size"],
        },
        {
            "id": bob_upload["id"],
            "name": bob_upload["name"],
            "mime": bob_upload["mime"],
            "size": bob_upload["size"],
        },
    ]
    msgs = [ChatMessage(
        role="user",
        content=multimodal,
        metadata={"attachments": attachment_metadata},
    )]
    assert manager.replace_messages(sid, msgs) is True

    # Simulate a real process restart: load_sessions() first caches metadata,
    # then get_session() takes the lazy hydration branch for message metadata.
    import core.session_manager as sm

    restarted = sm.SessionManager()
    assert sid in restarted.sessions
    assert restarted.sessions[sid].history == []
    assert restarted.sessions[sid].message_count == 1
    reloaded = restarted.get_session(sid)
    assert len(reloaded.history) == 1
    persisted_content = reloaded.history[0].content
    assert isinstance(persisted_content, str)
    assert "base64" not in persisted_content
    assert "what is this?" in persisted_content
    assert f"id={alice_upload['id']}" in persisted_content
    assert reloaded.history[0].metadata["attachments"] == attachment_metadata

    import src.settings as settings

    monkeypatch.setattr(
        settings,
        "get_setting",
        lambda key: [str(upload_dir)] if key == "tool_path_extra_roots" else None,
    )
    historical_ids = attachment_ids_from_messages(reloaded.history)
    restarted_handler = UploadHandler(str(tmp_path), str(upload_dir))
    manifest = build_uploaded_file_manifest(
        historical_ids,
        restarted_handler,
        owner=reloaded.owner,
    )

    assert historical_ids == [alice_upload["id"], bob_upload["id"]]
    assert [item["id"] for item in manifest] == [alice_upload["id"]]
    assert manifest[0]["read_policy"] == "owner_checked_upload"
    assert manifest[0]["uri"] == f"odysseus://attachment/{alice_upload['id']}"
    assert "path" not in manifest[0]
    assert restarted_handler.resolve_upload(bob_upload["id"], owner="alice") is None


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
