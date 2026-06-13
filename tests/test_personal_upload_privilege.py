import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import personal_routes


def _upload_endpoint():
    router = personal_routes.setup_personal_routes(_FakePersonalDocs(), None, True)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/personal/upload" and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("upload endpoint not found")


def _request(privileges):
    class _AuthManager:
        def get_privileges(self, user):
            assert user == "alice"
            return privileges

    return SimpleNamespace(
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=_AuthManager(),
            ),
        ),
        client=SimpleNamespace(host="203.0.113.10"),
    )


class _FakePersonalDocs:
    def __init__(self):
        self.added = []

    def add_directory(self, directory, index=False):
        self.added.append((directory, index))


class _FakeRAG:
    def __init__(self):
        self.docs = []

    def _split_into_chunks(self, text, chunk_size=500):
        return [text]

    def add_document(self, chunk, metadata):
        self.docs.append((chunk, metadata))
        return True


class _Upload:
    filename = "notes.txt"

    async def read(self, limit):
        return b"hello from upload"


def test_personal_upload_requires_document_privilege(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(
        personal_routes,
        "get_rag_manager",
        lambda: pytest.fail("RAG must not be touched before privilege passes"),
    )

    endpoint = _upload_endpoint()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(request=_request({"can_use_documents": False}), files=[]))

    assert exc.value.status_code == 403


def test_personal_upload_indexes_with_privileged_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)

    endpoint = _upload_endpoint()
    result = asyncio.run(
        endpoint(
            request=_request({"can_use_documents": True}),
            files=[_Upload()],
        )
    )

    assert result["success"] is True
    assert result["indexed_count"] == 1
    assert rag.docs[0][0] == "hello from upload"
    metadata = rag.docs[0][1]
    assert metadata["owner"] == "alice"
    assert Path(metadata["directory"]).name == "alice"
