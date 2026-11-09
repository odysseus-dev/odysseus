"""ZIP exports keep language extensions and place collision suffixes before them."""
import io
import zipfile

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Document
from routes.document import document_routes


@pytest.fixture
def exports(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'documents.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(document_routes, "SessionLocal", factory)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = FastAPI()

    @app.middleware("http")
    async def fixture_user(request: Request, call_next):
        request.state.current_user = "alice"
        return await call_next(request)

    app.include_router(document_routes.setup_document_routes(None))

    def add(doc_id, title, language, content="body\n", owner="alice"):
        with factory() as db:
            db.add(Document(id=doc_id, title=title, language=language,
                            current_content=content, owner=owner, is_active=True))
            db.commit()

    with TestClient(app) as client:
        yield client, add
    engine.dispose()


@pytest.mark.parametrize("title,language,filename", [
    ("config.local", "python", "config.local.py"),
    ("report.v2", "markdown", "report.v2.md"),
    ("data.2024", "json", "data.2024.json"),
    ("module.ts", "typescript", "module.ts"),
    ("SCRIPT.PY", "python", "SCRIPT.PY"),
    ("résumé.v2", "text", "résumé.v2.txt"),
    ("unknown.v2", "unknown", "unknown.v2.txt"),
    ("draft", None, "draft.txt"),
    ("project/report.v2", "markdown", "projectreport.v2.md"),
])
def test_export_uses_the_language_extension_even_for_dotted_titles(exports, title, language, filename):
    client, add = exports
    body = "Original text: α\n"
    add("doc", title, language, body)
    response = client.post("/api/documents/export-zip", json={"ids": ["doc"]})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [filename]
        assert archive.read(filename).decode() == body


@pytest.mark.parametrize("title", ["config.local", "config.local.py"])
def test_duplicate_names_keep_the_extension_at_the_end(exports, title):
    client, add = exports
    for i in range(3):
        add(str(i), title, "python", f"body {i}")
    response = client.post("/api/documents/export-zip", json={"ids": ["0", "1", "2"]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"config.local.py", "config.local-1.py", "config.local-2.py"}
        assert {archive.read(name).decode() for name in archive.namelist()} == {"body 0", "body 1", "body 2"}


def test_added_extension_collides_safely_with_an_existing_extension(exports):
    client, add = exports
    add("a", "report.v2", "markdown", "first")
    add("b", "report.v2.md", "markdown", "second")
    response = client.post("/api/documents/export-zip", json={"ids": ["a", "b"]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"report.v2.md", "report.v2-1.md"}
        assert {archive.read(name) for name in archive.namelist()} == {b"first", b"second"}


def test_foreign_documents_are_still_excluded_and_email_body_is_preserved(exports):
    client, add = exports
    add("owned", "reply.v2", "email", "Subject: Hello\n---\nBody")
    add("foreign", "private", "python", "secret", owner="bob")
    response = client.post("/api/documents/export-zip", json={"ids": ["owned", "foreign"]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["reply.v2.eml"]
        assert archive.read("reply.v2.eml") == b"Subject: Hello\r\n\r\nBody"
