"""Regression guard for #5558 — POST /api/personal/add_directory must not run
the indexing job on the event loop.

The handler is ``async def`` but called ``rag.index_personal_documents``
(os.walk + file reads + per-chunk embedding + Chroma inserts) inline, so
FastAPI ran the whole job on the event loop and every other request queued
behind it: indexing a real directory froze the UI and API for 25+ minutes.
``personal_docs_manager.add_directory`` sits in the same blocking section — it
triggers ``refresh_index()``, which re-extracts text across tracked dirs.

These tests build the real router with fake managers and compare the thread
the indexing work runs on against the event loop's thread.
"""
import asyncio
import os
import threading

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _serialization_probe():
    """Shared counter proving two critical sections never overlap."""
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def enter():
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])

    def leave():
        with lock:
            state["active"] -= 1

    return state, enter, leave


# Concurrency tests are `async def` (pyproject asyncio_mode="auto") and drive the
# ASGI app through httpx.ASGITransport + AsyncClient + asyncio.gather, NOT starlette
# TestClient + ThreadPoolExecutor: the job lock is an asyncio.Lock acquired in the
# async handler, and TestClient's portal-thread dispatch deadlocks against it (same
# reason test_notes_fail_closed_auth.py uses ASGITransport). asyncio.gather runs both
# requests on the test's own loop.
def _async_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

import routes.personal_routes as personal_routes
from core.middleware import require_admin
from src.auth_helpers import require_user


class _FakeRag:
    def __init__(self, record):
        self._record = record

    def index_personal_documents(self, directory, owner=None):
        self._record["index_thread"] = threading.get_ident()
        return {"success": True, "indexed_count": 3, "failed_count": 0}

    def _split_into_chunks(self, text, chunk_size=500):
        return [text]

    def add_document(self, chunk, metadata):
        self._record["add_document_thread"] = threading.get_ident()
        return True

    def delete_by_source(self, filepath):
        self._record["delete_thread"] = threading.get_ident()
        return 1


class _FakeDocsManager:
    def __init__(self, record):
        self._record = record
        self.index = []

    def add_directory(self, directory, *, index=True, owner=None):
        self._record["bookkeeping_thread"] = threading.get_ident()
        self._record["bookkeeping_index_flag"] = index

    def exclude_file(self, filepath):
        self._record["exclude_thread"] = threading.get_ident()


def _build_app(tmp_path, monkeypatch, record):
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: _FakeRag(record))

    app = FastAPI()
    app.include_router(
        personal_routes.setup_personal_routes(_FakeDocsManager(record), None, True)
    )
    app.dependency_overrides[require_user] = lambda: "tester"
    app.dependency_overrides[require_admin] = lambda: None

    @app.get("/loop-thread")
    async def loop_thread_probe():
        return {"thread": threading.get_ident()}

    return app


def test_indexing_runs_off_the_event_loop(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    # Context-manager client: one portal/event loop serves both requests, so
    # the probe and the POST are guaranteed to see the same loop thread.
    with TestClient(app) as client:
        loop_thread = client.get("/loop-thread").json()["thread"]
        resp = client.post(
            "/api/personal/add_directory", json={"directory": str(target)}
        )

    assert resp.status_code == 200
    assert record["index_thread"] != loop_thread, (
        "index_personal_documents ran on the event loop thread — every other "
        "request queues behind the indexing job (#5558)"
    )
    assert record["bookkeeping_thread"] != loop_thread, (
        "personal_docs_manager.add_directory (refresh_index) ran on the event "
        "loop thread"
    )


def test_response_and_bookkeeping_unchanged(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    client = TestClient(app)
    resp = client.post("/api/personal/add_directory", json={"directory": str(target)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["indexed_count"] == 3
    assert body["failed_count"] == 0
    assert body["directory"] == os.path.realpath(str(target))
    assert record["bookkeeping_index_flag"] is False


async def test_concurrent_add_directory_requests_serialize_indexing(tmp_path, monkeypatch):
    """Off-loop execution must not mean parallel index jobs: concurrent
    requests would race PersonalDocsManager's unsynchronized list mutations
    and file writes (save_directories/_save_excluded are plain open('w'))."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.2); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    for name in ("docs_a", "docs_b"):
        (tmp_path / name).mkdir()

    async with _async_client(app) as ac:
        results = await asyncio.gather(
            ac.post("/api/personal/add_directory", json={"directory": str(tmp_path / "docs_a")}),
            ac.post("/api/personal/add_directory", json={"directory": str(tmp_path / "docs_b")}),
        )

    assert all(r.status_code == 200 for r in results)
    assert state["max_active"] == 1, (
        f"{state['max_active']} index jobs ran in parallel — concurrent "
        "add_directory requests must serialize"
    )


def test_failed_indexing_still_returns_500(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    def _fail(directory, owner=None):
        return {"success": False, "message": "boom"}

    monkeypatch.setattr(_FakeRag, "index_personal_documents", staticmethod(_fail))

    client = TestClient(app)
    resp = client.post("/api/personal/add_directory", json={"directory": str(target)})
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]


async def test_add_and_remove_serialize(tmp_path, monkeypatch):
    """#5634: remove must hold the SAME job lock as add. Otherwise a remove
    running while an add job is in flight races PersonalDocsManager's
    unsynchronized list/index mutations — the inconsistent state the PR's
    'add/remove are serialized' guarantee claims to prevent."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_remove(self, directory):
        enter(); time.sleep(0.25); leave()

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeDocsManager, "remove_directory", _slow_remove, raising=False)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    (tmp_path / "docs_a").mkdir()
    (tmp_path / "docs_b").mkdir()

    async with _async_client(app) as ac:
        results = await asyncio.gather(
            ac.post("/api/personal/add_directory", json={"directory": str(tmp_path / "docs_a")}),
            ac.delete("/api/personal/remove_directory", params={"directory": str(tmp_path / "docs_b")}),
        )

    assert all(r.status_code == 200 for r in results)
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/remove critical sections overlapped — "
        "remove must hold the same index job lock as add"
    )


async def test_add_and_upload_serialize(tmp_path, monkeypatch):
    """#5634 follow-up: POST /upload writes chunks into the vector store and then
    calls personal_docs_manager.add_directory — the same vector/tracking state
    add_directory mutates. It must hold the SAME job lock, or an upload landing
    mid-add interleaves two writers over unsynchronized state."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_add_document(self, chunk, metadata):
        self._record["add_document_thread"] = threading.get_ident()
        enter(); time.sleep(0.25); leave()
        return True

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeRag, "add_document", _slow_add_document)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(personal_routes, "require_privilege", lambda request, key: "tester")
    (tmp_path / "docs_a").mkdir()

    async with _async_client(app) as ac:
        results = await asyncio.gather(
            ac.post("/api/personal/add_directory", json={"directory": str(tmp_path / "docs_a")}),
            ac.post("/api/personal/upload", files={"files": ("a.txt", b"hello world", "text/plain")}),
        )

    assert all(r.status_code == 200 for r in results)
    # The test coroutine runs on the event loop, so this IS the loop thread.
    assert record["add_document_thread"] != threading.get_ident(), (
        "rag.add_document ran on the event loop thread — chunk writes block "
        "every other request for the duration of the upload"
    )
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/upload critical sections overlapped — "
        "upload must hold the same index job lock as add"
    )


async def test_upload_processes_each_payload_before_reading_the_next(tmp_path, monkeypatch):
    """A multi-file upload must retain at most one capped payload at a time."""
    from starlette.datastructures import UploadFile as StarletteUploadFile

    reads = []
    original_read = StarletteUploadFile.read

    async def _recording_read(upload, size=-1):
        reads.append(upload.filename)
        return await original_read(upload, size)

    def _record_first_index(self, chunk, metadata):
        self._record.setdefault("reads_at_first_index", len(reads))
        return True

    monkeypatch.setattr(StarletteUploadFile, "read", _recording_read)
    monkeypatch.setattr(_FakeRag, "add_document", _record_first_index)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(personal_routes, "require_privilege", lambda request, key: "tester")

    files = [
        ("files", ("a.txt", b"alpha", "text/plain")),
        ("files", ("b.txt", b"bravo", "text/plain")),
        ("files", ("c.txt", b"charlie", "text/plain")),
    ]
    async with _async_client(app) as ac:
        response = await ac.post("/api/personal/upload", files=files)

    assert response.status_code == 200
    assert response.json()["uploaded"] == ["a.txt", "b.txt", "c.txt"]
    assert reads == ["a.txt", "b.txt", "c.txt"]
    assert record["reads_at_first_index"] == 1, (
        "all upload bodies were retained before worker processing began"
    )


async def test_add_and_delete_file_serialize(tmp_path, monkeypatch):
    """#5634 follow-up: DELETE /file removes chunks from the vector store and
    calls personal_docs_manager.exclude_file. Both mutate state add_directory
    also touches, so the delete must hold the SAME job lock as add."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_delete(self, filepath):
        self._record["delete_thread"] = threading.get_ident()
        enter(); time.sleep(0.25); leave()
        return 1

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeRag, "delete_by_source", _slow_delete)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
    (tmp_path / "docs_a").mkdir()
    doomed = tmp_path / "doomed.txt"
    doomed.write_text("bye")

    async with _async_client(app) as ac:
        results = await asyncio.gather(
            ac.post("/api/personal/add_directory", json={"directory": str(tmp_path / "docs_a")}),
            ac.delete("/api/personal/file", params={"filepath": str(doomed)}),
        )

    assert all(r.status_code == 200 for r in results)
    assert record["delete_thread"] != threading.get_ident(), (
        "rag.delete_by_source ran on the event loop thread"
    )
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/delete critical sections overlapped — "
        "delete must hold the same index job lock as add"
    )


async def test_reload_serializes_with_add(tmp_path, monkeypatch):
    """#5634: POST /reload rebuilds the index via refresh_index(); it must hold
    the same job lock so it cannot race an in-flight add job."""
    import time

    state, enter, leave = _serialization_probe()

    def _slow_index(self, directory, owner=None):
        enter(); time.sleep(0.25); leave()
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    def _slow_refresh(self):
        enter(); time.sleep(0.25); leave()

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)
    monkeypatch.setattr(_FakeDocsManager, "refresh_index", _slow_refresh, raising=False)

    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    (tmp_path / "docs_a").mkdir()

    async with _async_client(app) as ac:
        results = await asyncio.gather(
            ac.post("/api/personal/add_directory", json={"directory": str(tmp_path / "docs_a")}),
            ac.post("/api/personal/reload"),
        )

    assert all(r.status_code == 200 for r in results)
    assert state["max_active"] == 1, (
        f"{state['max_active']} add/reload critical sections overlapped — "
        "reload must hold the same index job lock as add"
    )
