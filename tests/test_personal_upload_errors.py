import importlib
import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _DocsManager:
    def __init__(self):
        self.directories = []

    def add_directory(self, directory, index=False):
        self.directories.append((directory, index))


class _FailingRag:
    def _split_into_chunks(self, text, chunk_size=500):
        return [text]

    def add_document(self, text, metadata):
        return False


def _load_personal_routes(monkeypatch, tmp_path):
    sys.modules.pop("routes.personal_routes", None)

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = []

    constants = types.ModuleType("core.constants")
    constants.BASE_DIR = str(tmp_path)
    constants.PERSONAL_DIR = str(tmp_path / "personal_docs")

    middleware = types.ModuleType("core.middleware")
    middleware.require_admin = lambda: None

    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.constants", constants)
    monkeypatch.setitem(sys.modules, "core.middleware", middleware)

    return importlib.import_module("routes.personal_routes")


def test_personal_upload_reports_when_every_chunk_fails(monkeypatch, tmp_path, request):
    personal_routes = _load_personal_routes(monkeypatch, tmp_path)
    request.addfinalizer(lambda: sys.modules.pop("routes.personal_routes", None))

    docs = _DocsManager()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: _FailingRag())

    app = FastAPI()
    app.include_router(personal_routes.setup_personal_routes(docs, None, False))

    client = TestClient(app)
    response = client.post(
        "/api/personal/upload",
        files={"files": ("notes.txt", b"hello from a local file", "text/plain")},
    )

    assert response.status_code == 503
    assert "no chunks could be indexed" in response.json()["detail"]
    assert docs.directories == [(str(tmp_path / "alice"), False)]
