import asyncio
import os
from pathlib import Path

from routes import personal_routes


class _FakePersonalDocs:
    def __init__(self):
        self.excluded = []

    def exclude_file(self, filepath):
        self.excluded.append(filepath)


class _FakeRAG:
    def __init__(self):
        self.deleted_sources = []

    def delete_by_source(self, filepath):
        self.deleted_sources.append(filepath)
        return 1


def _delete_endpoint(personal_docs):
    router = personal_routes.setup_personal_routes(personal_docs, None, True)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/personal/file" and "DELETE" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("DELETE /api/personal/file endpoint not found")


def test_delete_file_refuses_symlink_directory_escape(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    os.symlink(outside, uploads / "linked")

    docs = _FakePersonalDocs()
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(uploads))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)

    filepath = str(uploads / "linked" / "victim.txt")
    result = asyncio.run(_delete_endpoint(docs)(filepath=filepath, owner="alice", _admin=None))

    assert result["deleted_from_disk"] is False
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert docs.excluded == [filepath]
    assert rag.deleted_sources == [filepath]


def test_delete_file_removes_regular_file_inside_upload_root(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    uploaded_file = uploads / "alice" / "notes.txt"
    uploaded_file.parent.mkdir()
    uploaded_file.write_text("delete me", encoding="utf-8")

    docs = _FakePersonalDocs()
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(uploads))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)

    filepath = str(uploaded_file)
    result = asyncio.run(_delete_endpoint(docs)(filepath=filepath, owner="alice", _admin=None))

    assert result["deleted_from_disk"] is True
    assert not uploaded_file.exists()
    assert docs.excluded == [filepath]
    assert rag.deleted_sources == [filepath]


def test_delete_file_refuses_other_owners_upload(tmp_path, monkeypatch):
    # alice must not be able to delete a file living under bob's per-owner
    # upload subdir, even though it sits inside the shared uploads root.
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    victim = uploads / "bob" / "secret.txt"
    victim.parent.mkdir()
    victim.write_text("keep me", encoding="utf-8")

    docs = _FakePersonalDocs()
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(uploads))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)

    filepath = str(victim)
    result = asyncio.run(_delete_endpoint(docs)(filepath=filepath, owner="alice", _admin=None))

    assert result["deleted_from_disk"] is False
    assert victim.read_text(encoding="utf-8") == "keep me"
