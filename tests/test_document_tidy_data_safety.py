"""Tidy must preserve real document content regardless of its placeholder title."""

import asyncio
from datetime import datetime as RealDatetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.database import disposable_database


NOW = RealDatetime(2030, 1, 2, 12)
BODY = "Project plan: deliver the migration in stages, preserve customer records, and verify rollback.\n" * 30


class FrozenDatetime(RealDatetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2030, 1, 2, 12, tzinfo=tz)


def scheduled_tidy(actions):
    from src.builtin_actions import TaskNoop

    try:
        return asyncio.run(actions.run_document_tidy("alice"))
    except TaskNoop:
        return None


@pytest.mark.parametrize("entrypoint", ["manual", "scheduled"])
@pytest.mark.parametrize("title", ["Untitled", "Draft", "New document"])
def test_tidy_keeps_content_and_saved_history(tmp_path, monkeypatch, entrypoint, title):
    import core.database as database
    import routes.document.document_routes as routes
    import src.document_actions as actions

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(routes, "SessionLocal", factory)
        monkeypatch.setattr(routes, "datetime", FrozenDatetime)
        monkeypatch.setattr(actions, "datetime", FrozenDatetime)
        with factory() as db:
            for doc_id, doc_title in [("valued", title), ("control", "Project migration plan")]:
                db.add(database.Document(
                    id=doc_id, owner="alice", title=doc_title, current_content=BODY,
                    version_count=2, is_active=True, archived=False,
                    created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(seconds=1),
                ))
                for number, content in [(1, "Original project planning notes."), (2, BODY)]:
                    db.add(database.DocumentVersion(
                        id=f"{doc_id}-{number}", document_id=doc_id,
                        version_number=number, content=content, source="user",
                        summary="Saved checkpoint", created_at=NOW - timedelta(minutes=30-number),
                    ))
            db.commit()

        if entrypoint == "scheduled":
            scheduled_tidy(actions)
        else:
            app = FastAPI()

            @app.middleware("http")
            async def owner(request, call_next):
                request.state.current_user = "alice"
                return await call_next(request)

            app.include_router(routes.setup_document_routes(None))
            with TestClient(app) as client:
                response = client.post("/api/documents/tidy")
            assert response.status_code == 200, response.text

        with factory() as db:
            assert db.get(database.Document, "control") is not None
            versions = db.query(database.DocumentVersion).filter_by(document_id="valued").count()
            assert db.get(database.Document, "valued") is not None, (
                f"{entrypoint} deleted nonempty {title!r}; {versions} saved versions remain"
            )
            assert versions == 2


def test_scheduled_tidy_preserves_archived_document(tmp_path, monkeypatch):
    import core.database as database
    import src.document_actions as actions

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(actions, "datetime", FrozenDatetime)
        with factory() as db:
            db.add(database.Document(
                id="archived", owner="alice", title="Draft", current_content=BODY,
                created_at=NOW - timedelta(days=1), archived=True, is_active=True,
            ))
            db.commit()
        scheduled_tidy(actions)
        with factory() as db:
            assert db.get(database.Document, "archived") is not None


@pytest.fixture
def tidy_database(tmp_path, monkeypatch):
    import core.database as database
    import routes.document.document_routes as routes
    import src.document_actions as actions

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(routes, "SessionLocal", factory)
        monkeypatch.setattr(routes, "datetime", FrozenDatetime)
        monkeypatch.setattr(actions, "datetime", FrozenDatetime)
        yield factory


def seed_document(factory, doc_id, content, *, old_content="", **attributes):
    from core.database import Document, DocumentVersion

    fields = dict(owner="alice", title="Draft", current_content=content,
                  created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=1),
                  is_active=True, archived=False, version_count=2)
    fields.update(attributes)
    with factory() as db:
        db.add(Document(id=doc_id, **fields))
        for number, text in [(1, old_content), (2, content)]:
            db.add(DocumentVersion(id=f"{doc_id}-{number}", document_id=doc_id,
                                   version_number=number, content=text, source="user"))
        db.commit()


def run_tidy(entrypoint):
    import routes.document.document_routes as routes
    import src.document_actions as actions

    if entrypoint == "scheduled":
        return scheduled_tidy(actions)
    app = FastAPI()

    @app.middleware("http")
    async def owner(request, call_next):
        request.state.current_user = "alice"
        return await call_next(request)

    app.include_router(routes.setup_document_routes(None))
    with TestClient(app) as client:
        response = client.post("/api/documents/tidy")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("entrypoint", ["manual", "scheduled"])
@pytest.mark.parametrize("active", [True, False])
def test_blank_current_document_keeps_saved_content(tidy_database, entrypoint, active):
    from core.database import Document, DocumentVersion

    seed_document(tidy_database, "recoverable", "", old_content=BODY, is_active=active)
    run_tidy(entrypoint)
    with tidy_database() as db:
        assert db.get(Document, "recoverable") is not None
        assert db.get(DocumentVersion, "recoverable-1").content == BODY


@pytest.mark.parametrize("entrypoint", ["manual", "scheduled"])
def test_cleanup_removes_only_empty_history_for_current_owner(tidy_database, entrypoint):
    from core.database import Document

    seed_document(tidy_database, "empty", "   ")
    seed_document(tidy_database, "bob-empty", "   ", owner="bob")
    seed_document(tidy_database, "archived-empty", "", archived=True, is_active=False)
    run_tidy(entrypoint)
    with tidy_database() as db:
        assert db.get(Document, "empty") is None
        assert db.get(Document, "bob-empty") is not None
        assert db.get(Document, "archived-empty") is not None


@pytest.mark.parametrize("content", ["scratch", "> Original quoted email\n> Keep the attached figures"])
def test_scheduled_tidy_keeps_short_and_quoted_content(tidy_database, content):
    from core.database import Document

    seed_document(tidy_database, "saved", content)
    run_tidy("scheduled")
    with tidy_database() as db:
        assert db.get(Document, "saved").current_content == content


def test_duplicate_archive_preserves_each_documents_history(tidy_database):
    from core.database import Document, DocumentVersion

    seed_document(tidy_database, "first", BODY, old_content="First unique draft")
    seed_document(tidy_database, "second", BODY, old_content="Second unique draft")
    run_tidy("scheduled")
    with tidy_database() as db:
        documents = db.query(Document).all()
        assert len(documents) == 2
        assert sum(bool(doc.archived) for doc in documents) == 1
        assert db.get(DocumentVersion, "first-1").content == "First unique draft"
        assert db.get(DocumentVersion, "second-1").content == "Second unique draft"
        assert db.query(DocumentVersion).count() == 4
        # Restoring an archive is supported by the existing library API.
        archived_id = next(doc.id for doc in documents if doc.archived)
    import routes.document.document_routes as routes
    app = FastAPI()

    @app.middleware("http")
    async def owner(request, call_next):
        request.state.current_user = "alice"
        return await call_next(request)

    app.include_router(routes.setup_document_routes(None))
    with TestClient(app) as client:
        restored = client.post(f"/api/document/{archived_id}/archive?archived=false")
        assert restored.status_code == 200, restored.text
        assert restored.json()["archived"] is False
        assert len(client.get(f"/api/document/{archived_id}/versions").json()) == 2


@pytest.mark.parametrize("first,second", [
    ("ABC", "abc"),
    ("a\n  b", "a b"),
    ('<pdf_source upload_id="first" />', '<pdf_source upload_id="second" />'),
])
def test_different_content_is_not_archived_as_duplicate(tidy_database, first, second):
    from core.database import Document

    seed_document(tidy_database, "first", first)
    seed_document(tidy_database, "second", second)
    run_tidy("scheduled")
    with tidy_database() as db:
        assert db.query(Document).count() == 2
        assert not any(doc.archived for doc in db.query(Document).all())


def test_fresh_copy_is_not_archived_during_duplicate_pass(tidy_database):
    from core.database import Document

    seed_document(tidy_database, "older", BODY)
    seed_document(tidy_database, "newer", BODY, created_at=NOW - timedelta(minutes=1))
    run_tidy("scheduled")
    with tidy_database() as db:
        assert not any(doc.archived for doc in db.query(Document).all())


def test_ai_tidy_archives_without_erasing_versions(tidy_database, monkeypatch):
    import json
    import re
    import routes.document.document_routes as routes
    import src.llm_core as llm_core
    import src.task_endpoint as task_endpoint
    from core.database import Document, DocumentVersion

    seed_document(tidy_database, "classified", BODY, old_content="Original saved draft")
    seed_document(tidy_database, "keep", "Keep this content", title="Keep")
    seed_document(tidy_database, "bob", BODY, owner="bob")
    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint",
                        lambda **kwargs: ("http://127.0.0.1:1/v1", "synthetic", {}))

    async def classify(url, model, messages, **kwargs):
        lines = [line for line in messages[1]["content"].splitlines() if re.match(r"^\[\d+\]", line)]
        return json.dumps(["keep" if 'title="Keep"' in line else "junk" for line in lines])

    monkeypatch.setattr(llm_core, "llm_call_async", classify)
    app = FastAPI()

    @app.middleware("http")
    async def owner(request, call_next):
        request.state.current_user = "alice"
        return await call_next(request)

    app.include_router(routes.setup_document_routes(None))
    with TestClient(app) as client:
        response = client.post("/api/documents/ai-tidy")
        assert response.status_code == 200, response.text
        assert response.json()["archived"] == 1
        assert response.json()["deleted"] == 1  # Legacy Library removal count.
        assert response.json()["reviewed"] == 2
        assert "archived 1" in response.json()["message"]
        with tidy_database() as db:
            assert db.get(Document, "classified").archived
            assert not db.get(Document, "keep").archived
            assert not db.get(Document, "bob").archived
            assert db.get(DocumentVersion, "classified-1").content == "Original saved draft"
            assert db.query(DocumentVersion).filter_by(document_id="classified").count() == 2
        restored = client.post("/api/document/classified/archive?archived=false")
        assert restored.status_code == 200
        repeated = client.post("/api/documents/ai-tidy")
        assert repeated.json()["reviewed"] == 0
        assert not client.get("/api/document/classified").json()["archived"]
