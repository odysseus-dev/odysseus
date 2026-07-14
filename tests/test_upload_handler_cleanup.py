import asyncio
import concurrent.futures
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.database import (
    Base,
    ChatMessage as DbChatMessage,
    CalendarCal,
    CalendarEvent,
    Document,
    DocumentVersion,
    GalleryImage,
    Note,
    Session as DbSession,
)
from src.upload_handler import (
    UploadCleanupSafetyError,
    UploadHandler,
    extract_internal_upload_ids,
    reserve_message_upload_references,
    reserve_upload_references,
)
from tests.helpers.sqlite_db import make_temp_sqlite


OLD_TIMESTAMP = "2000-01-01T00:00:00"


class _AdminAuth:
    is_configured = True

    @staticmethod
    def is_admin(user):
        return user == "admin"


class _AdminRequest:
    headers = {}
    state = SimpleNamespace(current_user="admin")
    app = SimpleNamespace(state=SimpleNamespace(auth_manager=_AdminAuth()))


def _make_handler(tmp_path: Path) -> UploadHandler:
    base_dir = tmp_path / "base"
    upload_dir = tmp_path / "uploads"
    base_dir.mkdir()
    upload_dir.mkdir()
    return UploadHandler(str(base_dir), str(upload_dir))


def _seed_old_uploads(handler: UploadHandler, rows: list[dict]) -> dict[str, Path]:
    dated_dir = Path(handler.upload_dir) / "2000" / "01" / "01"
    dated_dir.mkdir(parents=True)
    index = {}
    paths = {}
    for row in rows:
        upload_id = row["id"]
        path = dated_dir / upload_id
        path.write_bytes(row.get("bytes", upload_id.encode("ascii")))
        info = {
            "id": upload_id,
            "path": str(path),
            "mime": row.get("mime", "application/octet-stream"),
            "size": path.stat().st_size,
            "name": row.get("name", upload_id),
            "original_name": row.get("name", upload_id),
            "hash": row["hash"],
            "checksum_sha256": row["hash"],
            "uploaded_at": row.get("uploaded_at", OLD_TIMESTAMP),
            "created_at": row.get("created_at", OLD_TIMESTAMP),
            "last_accessed": row.get("last_accessed", OLD_TIMESTAMP),
            "owner": row.get("owner", "alice"),
        }
        index[f"{info['owner']}:{info['hash']}"] = info
        paths[upload_id] = path

    Path(handler.upload_dir, "uploads.json").write_text(
        json.dumps(index),
        encoding="utf-8",
    )
    handler._index_cache = None
    return paths


def _manual_cleanup_endpoint(handler: UploadHandler, monkeypatch):
    import fastapi.dependencies.utils as dependency_utils
    from routes.upload_routes import router, setup_upload_routes

    monkeypatch.setattr(dependency_utils, "ensure_multipart_is_installed", lambda: None)
    before = len(router.routes)
    setup_upload_routes(handler)
    return {
        route.endpoint.__name__: route.endpoint
        for route in router.routes[before:]
    }["manual_cleanup"]


def _reference_database(monkeypatch, *, upload_id: str, gallery_hash: str = None):
    from routes import upload_routes

    SessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)
    db = SessionLocal()
    try:
        db.add(DbSession(
            id="session-1",
            name="Cleanup regression",
            endpoint_url="http://localhost",
            model="test-model",
            owner="alice",
        ))
        db.add(DbChatMessage(
            id="message-1",
            session_id="session-1",
            role="user",
            content=f"[Attachment: retained.png | id={upload_id} | mime=image/png]",
            meta_data=json.dumps({
                "attachments": [{
                    "id": upload_id,
                    "name": "retained.png",
                    "mime": "image/png",
                    "size": 8,
                }]
            }),
        ))
        if gallery_hash:
            db.add(GalleryImage(
                id="gallery-cleanup-reference",
                filename="abcdef123456.png",
                prompt="Chat upload",
                owner="alice",
                file_hash=gallery_hash,
            ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(upload_routes, "SessionLocal", SessionLocal)
    return engine, tmpfile


def test_admin_cleanup_preserves_referenced_upload_and_reconciles_deleted_row(
    tmp_path,
    monkeypatch,
):
    handler = _make_handler(tmp_path)
    referenced_id = "a" * 32 + ".png"
    unreferenced_id = "b" * 32 + ".txt"
    gallery_id = "7" * 32 + ".png"
    gallery_hash = "7" * 64
    paths = _seed_old_uploads(handler, [
        {
            "id": referenced_id,
            "hash": "1" * 64,
            "mime": "image/png",
        },
        {
            "id": unreferenced_id,
            "hash": "2" * 64,
            "mime": "text/plain",
        },
        {
            "id": gallery_id,
            "hash": gallery_hash,
            "mime": "image/png",
        },
    ])
    engine, tmpfile = _reference_database(
        monkeypatch,
        upload_id=referenced_id,
        gallery_hash=gallery_hash,
    )

    try:
        response = asyncio.run(
            _manual_cleanup_endpoint(handler, monkeypatch)(_AdminRequest())
        )
    finally:
        engine.dispose()
        tmpfile.close()
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass

    assert response == {"status": "success", "files_cleaned": 1}
    assert paths[referenced_id].is_file()
    referenced_info = handler.get_upload_info(referenced_id)
    assert referenced_info is not None
    assert handler.resolve_upload(referenced_id, owner="alice") is not None
    assert paths[gallery_id].is_file()
    assert handler.get_upload_info(gallery_id) is not None

    assert not paths[unreferenced_id].exists()
    assert handler.get_upload_info(unreferenced_id) is None
    assert handler.resolve_upload(unreferenced_id, owner="alice") is None

    live_index = json.loads(
        Path(handler.upload_dir, "uploads.json").read_text(encoding="utf-8")
    )
    assert {info["id"] for info in live_index.values()} == {
        referenced_id,
        gallery_id,
    }
    backup_index = json.loads(
        Path(handler.upload_dir, "uploads.json.bak").read_text(encoding="utf-8")
    )
    assert {info["id"] for info in backup_index.values()} == {
        referenced_id,
        gallery_id,
    }

    # Recovery must not resurrect the deliberately deleted row.
    Path(handler.upload_dir, "uploads.json").write_text("{broken", encoding="utf-8")
    handler._index_cache = None
    assert handler.get_upload_info(unreferenced_id) is None
    assert paths[referenced_id].parent.is_dir()


def test_cleanup_retains_upload_and_all_rows_when_index_rows_disagree(tmp_path):
    handler = _make_handler(tmp_path)
    upload_id = "c" * 32 + ".txt"
    path = _seed_old_uploads(handler, [{
        "id": upload_id,
        "hash": "1" * 64,
        "mime": "text/plain",
        "owner": "alice",
    }])[upload_id]
    index_path = Path(handler.upload_dir, "uploads.json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    alice_row = next(iter(index.values()))
    index["bob:" + "2" * 64] = {
        **alice_row,
        "owner": "bob",
        "hash": "2" * 64,
        "checksum_sha256": "2" * 64,
    }
    index_path.write_text(json.dumps(index), encoding="utf-8")
    handler._index_cache = None

    assert handler.cleanup_old_uploads(set(), set()) == 0
    assert path.is_file()
    assert json.loads(index_path.read_text(encoding="utf-8")) == index


def test_cleanup_retains_lone_row_without_authoritative_lifecycle_metadata(tmp_path):
    handler = _make_handler(tmp_path)
    upload_id = "6" * 32 + ".txt"
    path = _seed_old_uploads(handler, [{
        "id": upload_id,
        "hash": "6" * 64,
        "mime": "text/plain",
    }])[upload_id]
    index_path = Path(handler.upload_dir, "uploads.json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    row = next(iter(index.values()))
    for field in (
        "owner",
        "hash",
        "checksum_sha256",
        "uploaded_at",
        "created_at",
        "last_accessed",
    ):
        row.pop(field)
    index_path.write_text(json.dumps(index), encoding="utf-8")
    handler._index_cache = None

    assert handler.cleanup_old_uploads(set(), set()) == 0
    assert path.is_file()
    assert json.loads(index_path.read_text(encoding="utf-8")) == index


def test_reservation_and_cleanup_are_serialized_without_dangling_references(
    tmp_path,
    monkeypatch,
):
    # Writer wins: reservation holds the shared index lock, refreshes access,
    # then cleanup observes the refreshed row and preserves the file.
    writer_root = tmp_path / "writer-wins"
    writer_root.mkdir()
    writer_handler = _make_handler(writer_root)
    upload_id = "2" * 32 + ".txt"
    writer_path = _seed_old_uploads(writer_handler, [{
        "id": upload_id,
        "hash": "2" * 64,
        "mime": "text/plain",
    }])[upload_id]
    write_entered = threading.Event()
    release_write = threading.Event()
    real_atomic_write = writer_handler._atomic_write_json

    def blocking_reservation_write(path, data, *, sync_backup=False):
        refreshed = any(
            isinstance(row, dict) and row.get("last_accessed") != OLD_TIMESTAMP
            for row in data.values()
        )
        if sync_backup and refreshed and not write_entered.is_set():
            write_entered.set()
            assert release_write.wait(5)
        return real_atomic_write(path, data, sync_backup=sync_backup)

    monkeypatch.setattr(writer_handler, "_atomic_write_json", blocking_reservation_write)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        reserve_future = pool.submit(
            writer_handler.reserve_upload,
            upload_id,
            owner="alice",
        )
        assert write_entered.wait(5)
        cleanup_future = pool.submit(writer_handler.cleanup_old_uploads, set(), set())
        release_write.set()
        assert reserve_future.result(timeout=5) is not None
        assert cleanup_future.result(timeout=5) == 0
    assert writer_path.is_file()

    # Cleanup wins: reservation cannot pass the same lock until the row and
    # bytes are gone, then fails so a caller cannot commit a dangling reference.
    cleanup_root = tmp_path / "cleanup-wins"
    cleanup_root.mkdir()
    cleanup_handler = _make_handler(cleanup_root)
    cleanup_path = _seed_old_uploads(cleanup_handler, [{
        "id": upload_id,
        "hash": "3" * 64,
        "mime": "text/plain",
    }])[upload_id]
    remove_entered = threading.Event()
    release_remove = threading.Event()
    real_remove = os.remove

    def blocking_remove(candidate):
        if os.path.realpath(candidate) == os.path.realpath(cleanup_path):
            remove_entered.set()
            assert release_remove.wait(5)
        return real_remove(candidate)

    monkeypatch.setattr("src.upload_handler.os.remove", blocking_remove)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        cleanup_future = pool.submit(cleanup_handler.cleanup_old_uploads, set(), set())
        assert remove_entered.wait(5)
        reserve_future = pool.submit(
            cleanup_handler.reserve_upload,
            upload_id,
            owner="alice",
        )
        release_remove.set()
        assert cleanup_future.result(timeout=5) == 1
        assert reserve_future.result(timeout=5) is None
    assert not cleanup_path.exists()


def test_admin_cleanup_reference_discovery_failure_returns_503_without_deleting(
    tmp_path,
    monkeypatch,
):
    from routes import upload_routes

    handler = _make_handler(tmp_path)
    upload_id = "d" * 32 + ".png"
    path = _seed_old_uploads(handler, [
        {"id": upload_id, "hash": "4" * 64, "mime": "image/png"},
    ])[upload_id]

    def fail_reference_scan():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        upload_routes,
        "_collect_persisted_upload_references",
        fail_reference_scan,
    )
    endpoint = _manual_cleanup_endpoint(handler, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_AdminRequest()))

    assert exc.value.status_code == 503
    assert path.is_file()
    assert handler.get_upload_info(upload_id) is not None


def test_cleanup_restores_index_when_file_removal_fails(tmp_path, monkeypatch):
    handler = _make_handler(tmp_path)
    upload_id = "e" * 32 + ".txt"
    path = _seed_old_uploads(handler, [
        {
            "id": upload_id,
            "hash": "5" * 64,
            "mime": "text/plain",
        },
    ])[upload_id]

    real_remove = os.remove

    def fail_target_remove(candidate):
        if os.path.realpath(candidate) == os.path.realpath(path):
            raise PermissionError("file is in use")
        return real_remove(candidate)

    monkeypatch.setattr("src.upload_handler.os.remove", fail_target_remove)

    assert handler.cleanup_old_uploads(set(), set()) == 0
    assert path.is_file()
    assert handler.get_upload_info(upload_id) is not None
    assert any(
        info["id"] == upload_id
        for info in json.loads(
            Path(handler.upload_dir, "uploads.json").read_text(encoding="utf-8")
        ).values()
    )
    assert any(
        info["id"] == upload_id
        for info in json.loads(
            Path(handler.upload_dir, "uploads.json.bak").read_text(encoding="utf-8")
        ).values()
    )


def test_admin_cleanup_with_corrupt_index_returns_503_and_fails_closed(
    tmp_path,
    monkeypatch,
):
    from routes import upload_routes

    handler = _make_handler(tmp_path)
    upload_id = "9" * 32 + ".png"
    path = _seed_old_uploads(handler, [
        {"id": upload_id, "hash": "9" * 64, "mime": "image/png"},
    ])[upload_id]
    Path(handler.upload_dir, "uploads.json").write_text(
        '{"alice:broken": {',
        encoding="utf-8",
    )
    handler._index_cache = None

    monkeypatch.setattr(
        upload_routes,
        "_collect_persisted_upload_references",
        lambda: (set(), set()),
    )
    endpoint = _manual_cleanup_endpoint(handler, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_AdminRequest()))

    assert exc.value.status_code == 503
    assert path.is_file()


def test_cleanup_with_missing_live_index_fails_closed(tmp_path):
    handler = _make_handler(tmp_path)
    upload_id = "8" * 32 + ".png"
    dated_dir = Path(handler.upload_dir) / "2000" / "01" / "01"
    dated_dir.mkdir(parents=True)
    path = dated_dir / upload_id
    path.write_bytes(b"unindexed bytes")

    with pytest.raises(UploadCleanupSafetyError):
        handler.cleanup_old_uploads(set(), set())

    assert path.is_file()


def test_reference_discovery_covers_all_durable_upload_stores(
    monkeypatch,
):
    from routes import upload_routes

    document_id = "f" * 32 + ".pdf"
    version_id = "1" * 32 + ".pdf"
    note_upload_id = "3" * 32 + ".png"
    note_color_id = "2" * 32 + ".png"
    calendar_upload_id = "4" * 32 + ".png"
    event_upload_id = "5" * 32 + ".png"
    event_description_id = "7" * 32 + ".txt"
    event_location_id = "8" * 32 + ".png"
    gallery_hash = "6" * 64
    SessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)
    db = SessionLocal()
    try:
        db.add(DbSession(
            id="session-2",
            name="Reference sources",
            endpoint_url="http://localhost",
            model="test-model",
            owner="alice",
        ))
        db.add(Document(
            id="document-1",
            session_id="session-2",
            title="PDF",
            current_content=f'<!-- pdf_source upload_id="{document_id}" -->',
            owner="alice",
        ))
        db.add(DocumentVersion(
            id="version-1",
            document_id="document-1",
            version_number=1,
            content=f'<!-- pdf_form_source upload_id="{version_id}" fields="1" -->',
        ))
        db.add(GalleryImage(
            id="gallery-1",
            # Gallery filenames are normally generated 12-hex names, so this
            # record proves retention comes from its stored content hash.
            filename="abcdef123456.png",
            prompt="Chat upload",
            owner="alice",
            file_hash=gallery_hash,
        ))
        db.add(Note(
            id="note-1",
            owner="alice",
            title="Photo note",
            image_url=f"/api/upload/{note_upload_id}",
            color=f"odysseus://attachment/{note_color_id}",
        ))
        db.add(CalendarCal(
            id="calendar-1",
            owner="alice",
            name="Personal",
            color=f"/api/upload/{calendar_upload_id}",
        ))
        db.add(CalendarEvent(
            uid="event-1",
            calendar_id="calendar-1",
            summary="Photo event",
            dtstart=datetime(2026, 7, 10, 12, 0),
            dtend=datetime(2026, 7, 10, 13, 0),
            color=f"/api/upload/{event_upload_id}",
            description=f"Notes: odysseus://attachment/{event_description_id}",
            location=f"/api/upload/{event_location_id}",
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(upload_routes, "SessionLocal", SessionLocal)
    try:
        referenced_ids, referenced_hashes = (
            upload_routes._collect_persisted_upload_references()
        )
    finally:
        engine.dispose()
        tmpfile.close()
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass

    assert {
        document_id,
        version_id,
        note_upload_id,
        note_color_id,
        calendar_upload_id,
        event_upload_id,
        event_description_id,
        event_location_id,
    } <= referenced_ids
    assert gallery_hash in referenced_hashes


def test_write_reservation_extracts_only_explicit_internal_references():
    upload_id = "a" * 32 + ".png"
    checksum_like_text = "b" * 32

    assert extract_internal_upload_ids(checksum_like_text) == set()
    assert extract_internal_upload_ids(f"sha={checksum_like_text}") == set()
    assert extract_internal_upload_ids({
        "image": f"/api/upload/{upload_id}",
        "nested": [f"odysseus://attachment/{upload_id}"],
    }) == {upload_id}
    assert extract_internal_upload_ids(
        f'<!-- pdf_source upload_id="{upload_id}" -->'
    ) == {upload_id}
    assert extract_internal_upload_ids(
        f"[Attachment: photo.png | id={upload_id} | mime=image/png]"
    ) == {upload_id}
    extensionless_id = "c" * 32
    assert extract_internal_upload_ids(
        f"See /api/upload/{extensionless_id}. Then continue."
    ) == {extensionless_id}
    assert extract_internal_upload_ids(
        f"Attachment: odysseus://attachment/{extensionless_id}: ready"
    ) == {extensionless_id}
    assert extract_internal_upload_ids(f"/api/upload/{upload_id}/extra") == set()


def test_reservation_never_uses_admin_override(tmp_path):
    handler = _make_handler(tmp_path)
    upload_id = "c" * 32 + ".txt"
    _seed_old_uploads(handler, [{
        "id": upload_id,
        "hash": "c" * 64,
        "mime": "text/plain",
        "owner": "alice",
    }])

    assert reserve_upload_references(
        handler,
        "alice",
        f"odysseus://attachment/{upload_id}",
    ) is None
    assert reserve_upload_references(
        handler,
        "admin",
        f"odysseus://attachment/{upload_id}",
    ) == upload_id
    assert handler.reserve_upload(
        upload_id,
        owner="admin",
        auth_manager=_AdminAuth(),
        allow_admin=False,
    ) is None
    assert reserve_message_upload_references(
        handler,
        "admin",
        "legacy attachment metadata",
        {"attachments": [{"id": upload_id, "name": "owned.txt"}]},
    ) == upload_id


def test_remaining_durable_writers_reserve_before_commit(monkeypatch):
    import core.database as database
    import core.session_manager as session_manager_module
    import src.database as legacy_database
    from core.models import ChatMessage
    from core.session_manager import SessionManager
    from src import tool_utils
    from src.agent_tools.document_tools import EditDocumentTool
    from src.tools.calendar import do_manage_calendar
    from src.tools.notes import do_manage_notes

    upload_id = "6" * 32 + ".png"

    class RejectingHandler:
        def reserve_upload(self, _candidate, **_kwargs):
            return None

    handler = RejectingHandler()
    monkeypatch.setattr(tool_utils, "_upload_handler", handler)

    SessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(legacy_database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(legacy_database, "Document", Document, raising=False)
    monkeypatch.setattr(
        legacy_database,
        "DocumentVersion",
        DocumentVersion,
        raising=False,
    )
    monkeypatch.setattr(session_manager_module, "SessionLocal", SessionLocal)
    db = SessionLocal()
    try:
        db.add(DbSession(
            id="writer-session",
            name="Writer coverage",
            endpoint_url="http://localhost",
            model="test-model",
            owner="alice",
        ))
        db.add(Document(
            id="email-document",
            session_id="writer-session",
            title="New Email",
            language="email",
            current_content="To: team@example.test\nSubject: Status\n---\nOld body",
            version_count=1,
            owner="alice",
        ))
        db.commit()

        manager = SessionManager()
        manager.upload_handler = handler
        manager._persist_message(
            "writer-session",
            ChatMessage(
                "user",
                "attachment",
                metadata={"attachments": [{"id": upload_id}]},
            ),
        )

        document_result = asyncio.run(EditDocumentTool().execute(
            "<<<FIND>>>\n\n<<<REPLACE>>>\n"
            f"See /api/upload/{upload_id}\n<<<END>>>",
            {"doc_id": "email-document", "owner": "alice"},
        ))
        assert document_result["exit_code"] == 1
        assert "no longer available" in document_result["error"]

        calendar_result = asyncio.run(do_manage_calendar(
            json.dumps({
                "action": "create_event",
                "summary": "Attachment review",
                "dtstart": "2026-07-12T12:00:00",
                "description": f"See /api/upload/{upload_id}",
            }),
            owner="alice",
        ))
        assert calendar_result["exit_code"] == 1
        assert "no longer available" in calendar_result["error"]

        note_result = asyncio.run(do_manage_notes(
            json.dumps({
                "action": "add",
                "title": "Attachment note",
                "content": f"See /api/upload/{upload_id}",
            }),
            owner="alice",
        ))
        assert note_result["exit_code"] == 1
        assert "no longer available" in note_result["error"]

        verify = SessionLocal()
        try:
            assert verify.query(DbChatMessage).count() == 0
            stored_doc = verify.query(Document).filter(Document.id == "email-document").one()
            assert stored_doc.current_content.endswith("Old body")
            assert verify.query(CalendarEvent).count() == 0
            assert verify.query(Note).count() == 0
        finally:
            verify.close()
    finally:
        db.close()
        engine.dispose()
        tmpfile.close()
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


def test_note_calendar_and_document_routes_reserve_before_database_writes(monkeypatch):
    from routes.calendar_routes import EventCreate, setup_calendar_routes
    from routes import document_routes
    from routes.document_helpers import DocumentCreate
    from routes.note_routes import NoteCreate, setup_note_routes
    from src import auth_helpers

    upload_id = "d" * 32 + ".png"

    class RejectingHandler:
        def __init__(self):
            self.calls = []

        def reserve_upload(self, candidate, **kwargs):
            self.calls.append((candidate, kwargs))
            return None

    request = SimpleNamespace(
        state=SimpleNamespace(current_user="alice", api_token=False),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    note_handler = RejectingHandler()
    note_router = setup_note_routes(upload_handler=note_handler)
    create_note = next(
        route.endpoint for route in note_router.routes
        if route.endpoint.__name__ == "create_note"
    )
    with pytest.raises(HTTPException) as note_error:
        create_note(
            request,
            NoteCreate(image_url=f"/api/upload/{upload_id}"),
        )
    assert note_error.value.status_code == 409
    assert note_handler.calls == [
        (upload_id, {"owner": "alice", "allow_admin": False})
    ]

    calendar_handler = RejectingHandler()
    calendar_router = setup_calendar_routes(upload_handler=calendar_handler)
    create_event = next(
        route.endpoint for route in calendar_router.routes
        if route.endpoint.__name__ == "create_event"
    )
    with pytest.raises(HTTPException) as calendar_error:
        asyncio.run(create_event(
            request,
            EventCreate(
                summary="Photo",
                dtstart="2026-07-10T12:00:00",
                color=f"odysseus://attachment/{upload_id}",
            ),
        ))
    assert calendar_error.value.status_code == 409
    assert calendar_handler.calls == [
        (upload_id, {"owner": "alice", "allow_admin": False})
    ]

    class EmptyDb:
        @staticmethod
        def close():
            return None

    document_handler = RejectingHandler()
    monkeypatch.setattr(document_routes, "SessionLocal", EmptyDb)
    monkeypatch.setattr(
        auth_helpers,
        "require_privilege",
        lambda _request, _privilege: "alice",
    )
    document_router = document_routes.setup_document_routes(
        SimpleNamespace(),
        document_handler,
    )
    create_document = next(
        route.endpoint for route in document_router.routes
        if route.endpoint.__name__ == "create_document"
    )
    with pytest.raises(HTTPException) as document_error:
        asyncio.run(create_document(
            request,
            DocumentCreate(
                language="markdown",
                content=f"![image](/api/upload/{upload_id})",
            ),
        ))
    assert document_error.value.status_code == 409
    assert document_handler.calls == [
        (upload_id, {"owner": "alice", "allow_admin": False})
    ]
