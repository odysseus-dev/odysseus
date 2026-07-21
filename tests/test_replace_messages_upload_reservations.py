"""Upload lifecycle guarantees for compaction's replace_messages path."""

import concurrent.futures
import json
import os
import threading
import uuid

import pytest
from sqlalchemy import event

import core.database as cdb
import core.session_manager as session_manager_module
from core.models import ChatMessage
from src.upload_handler import UploadHandler
from tests.helpers.sqlite_db import make_temp_sqlite


OLD_TIMESTAMP = "2000-01-01T00:00:00"


@pytest.fixture
def manager_db(monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(cdb.Base.metadata)
    monkeypatch.setattr(session_manager_module, "SessionLocal", SessionLocal)
    manager = session_manager_module.SessionManager.__new__(
        session_manager_module.SessionManager
    )
    manager.sessions = {}
    manager.upload_handler = None
    try:
        yield manager, SessionLocal, engine
    finally:
        engine.dispose()
        tmpfile.close()
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


def _seed_session(SessionLocal, *, owner="alice", content="existing durable history"):
    session_id = "replace-" + uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.add(cdb.Session(
            id=session_id,
            owner=owner,
            name="Compaction reservation regression",
            model="test-model",
            endpoint_url="http://localhost:11434",
            archived=False,
            message_count=1,
        ))
        db.add(cdb.ChatMessage(
            id="message-" + uuid.uuid4().hex,
            session_id=session_id,
            role="user",
            content=content,
            meta_data=json.dumps({"source": "before-replacement"}),
        ))
        db.commit()
    finally:
        db.close()
    return session_id


def _attachment_message(upload_id, text):
    return ChatMessage(
        role="user",
        content=text,
        metadata={
            "attachments": [{
                "id": upload_id,
                "name": f"{text}.txt",
                "mime": "text/plain",
                "size": len(text),
            }]
        },
    )


def _durable_messages(SessionLocal, session_id):
    db = SessionLocal()
    try:
        return [
            (message.role, message.content, message.meta_data)
            for message in db.query(cdb.ChatMessage)
            .filter(cdb.ChatMessage.session_id == session_id)
            .order_by(cdb.ChatMessage.timestamp, cdb.ChatMessage.id)
            .all()
        ]
    finally:
        db.close()


def test_replace_messages_reserves_every_incoming_attachment_before_delete(
    manager_db,
    monkeypatch,
):
    manager, SessionLocal, engine = manager_db
    session_id = _seed_session(SessionLocal)
    manager.upload_handler = object()
    incoming = [
        _attachment_message("1" * 32 + ".txt", "first"),
        _attachment_message("2" * 32 + ".txt", "second"),
    ]
    message_mutations = []
    reservation_calls = []

    def record_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lstrip().upper()
        if normalized.startswith(("DELETE FROM CHAT_MESSAGES", "INSERT INTO CHAT_MESSAGES")):
            message_mutations.append(normalized.split(maxsplit=1)[0])

    def reserve(handler, owner, content, metadata):
        assert message_mutations == []
        reservation_calls.append((handler, owner, content, metadata))
        return None

    event.listen(engine, "before_cursor_execute", record_sql)
    monkeypatch.setattr(
        session_manager_module,
        "reserve_message_upload_references",
        reserve,
    )
    try:
        assert manager.replace_messages(session_id, incoming) is True
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)

    assert [call[2] for call in reservation_calls] == ["first", "second"]
    assert all(call[0] is manager.upload_handler for call in reservation_calls)
    assert all(call[1] == "alice" for call in reservation_calls)
    assert message_mutations == ["DELETE", "INSERT"]


def test_replace_messages_reservation_failure_leaves_durable_history_unchanged(
    manager_db,
    monkeypatch,
):
    manager, SessionLocal, engine = manager_db
    session_id = _seed_session(SessionLocal)
    before = _durable_messages(SessionLocal, session_id)
    manager.upload_handler = object()
    missing_upload_id = "4" * 32 + ".txt"
    incoming = [
        _attachment_message("3" * 32 + ".txt", "available"),
        _attachment_message(missing_upload_id, "missing"),
    ]
    calls = []
    message_mutations = []

    def record_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lstrip().upper()
        if normalized.startswith(("DELETE FROM CHAT_MESSAGES", "INSERT INTO CHAT_MESSAGES")):
            message_mutations.append(normalized.split(maxsplit=1)[0])

    def reserve(_handler, _owner, content, _metadata):
        calls.append(content)
        return missing_upload_id if content == "missing" else None

    event.listen(engine, "before_cursor_execute", record_sql)
    monkeypatch.setattr(
        session_manager_module,
        "reserve_message_upload_references",
        reserve,
    )
    try:
        assert manager.replace_messages(session_id, incoming) is False
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)

    assert calls == ["available", "missing"]
    assert message_mutations == []
    assert _durable_messages(SessionLocal, session_id) == before
    assert [message.content for message in manager.sessions[session_id].history] == [
        "existing durable history"
    ]
    assert all("_db_id" not in (message.metadata or {}) for message in incoming)


def test_cleanup_cannot_delete_attachment_during_concurrent_compaction_replacement(
    manager_db,
    monkeypatch,
    tmp_path,
):
    manager, SessionLocal, _engine = manager_db
    session_id = _seed_session(SessionLocal)
    base_dir = tmp_path / "base"
    upload_dir = tmp_path / "uploads"
    base_dir.mkdir()
    upload_dir.mkdir()
    handler = UploadHandler(str(base_dir), str(upload_dir))
    manager.upload_handler = handler

    upload_id = "5" * 32 + ".txt"
    upload_hash = "6" * 64
    dated_dir = upload_dir / "2000" / "01" / "01"
    dated_dir.mkdir(parents=True)
    upload_path = dated_dir / upload_id
    upload_path.write_text("attachment retained by compaction", encoding="utf-8")
    upload_row = {
        "id": upload_id,
        "path": str(upload_path),
        "mime": "text/plain",
        "size": upload_path.stat().st_size,
        "name": "compaction.txt",
        "original_name": "compaction.txt",
        "hash": upload_hash,
        "checksum_sha256": upload_hash,
        "uploaded_at": OLD_TIMESTAMP,
        "created_at": OLD_TIMESTAMP,
        "last_accessed": OLD_TIMESTAMP,
        "owner": "alice",
    }
    (upload_dir / "uploads.json").write_text(
        json.dumps({f"alice:{upload_hash}": upload_row}),
        encoding="utf-8",
    )
    handler._index_cache = None

    reservation_write_entered = threading.Event()
    release_reservation_write = threading.Event()
    real_atomic_write = handler._atomic_write_json

    def block_reservation_write(path, data, *, sync_backup=False):
        refreshed = any(
            isinstance(row, dict)
            and row.get("id") == upload_id
            and row.get("last_accessed") != OLD_TIMESTAMP
            for row in data.values()
        )
        if sync_backup and refreshed and not reservation_write_entered.is_set():
            reservation_write_entered.set()
            assert release_reservation_write.wait(5)
        return real_atomic_write(path, data, sync_backup=sync_backup)

    monkeypatch.setattr(handler, "_atomic_write_json", block_reservation_write)
    incoming = [_attachment_message(upload_id, "retained after compaction")]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        replace_future = pool.submit(manager.replace_messages, session_id, incoming)
        assert reservation_write_entered.wait(5)
        cleanup_future = pool.submit(handler.cleanup_old_uploads, set(), set())
        try:
            with pytest.raises(concurrent.futures.TimeoutError):
                cleanup_future.result(timeout=0.1)
        finally:
            release_reservation_write.set()

        assert replace_future.result(timeout=5) is True
        assert cleanup_future.result(timeout=5) == 0

    assert upload_path.is_file()
    assert handler.resolve_upload(upload_id, owner="alice") is not None
    durable = _durable_messages(SessionLocal, session_id)
    assert len(durable) == 1
    assert json.loads(durable[0][2])["attachments"][0]["id"] == upload_id
