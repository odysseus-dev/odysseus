"""Autosaves must preserve initial, explicitly saved and restored versions."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.database import disposable_database


_NOW = datetime(2030, 10, 7, 12, tzinfo=timezone.utc)


class _Clock:
    def __init__(self):
        self.current = _NOW

    def now(self, tz=None):
        return self.current if tz else self.current.replace(tzinfo=None)


@pytest.fixture
def api(tmp_path, monkeypatch):
    import routes.document.document_routes as routes

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(routes, "SessionLocal", factory)
        clock = _Clock()
        monkeypatch.setattr(routes, "datetime", clock)
        app = FastAPI()

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.current_user = "alice"
            return await call_next(request)

        app.include_router(routes.setup_document_routes(SimpleNamespace()))
        with TestClient(app) as client:
            yield client, factory, clock


def _create_document(client):
    created = client.post("/api/document", json={
        "title": "Test document", "language": "markdown", "content": "Original text",
    })
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _anchor_latest(factory, doc_id, at=_NOW):
    from core.database import DocumentVersion

    with factory() as db:
        version = db.query(DocumentVersion).filter(
            DocumentVersion.document_id == doc_id,
        ).order_by(DocumentVersion.version_number.desc()).first()
        version.created_at = at.replace(tzinfo=None)
        snapshot = (version.version_number, version.content)
        db.commit()
        return snapshot


@pytest.mark.parametrize("checkpoint", ["initial", "saved", "restored"])
def test_autosave_preserves_checkpoint(api, checkpoint):
    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    if checkpoint in {"saved", "restored"}:
        saved = client.put(path, json={
            "content": "Saved text", "force_version": True, "summary": "Saved version",
        })
        assert saved.status_code == 200, saved.text
    if checkpoint == "restored":
        restored = client.post(f"{path}/restore/1")
        assert restored.status_code == 200, restored.text

    # Put the latest version at a fixed time, inside the 60-second window.
    number, expected = _anchor_latest(factory, doc_id)

    updated = client.put(path, json={"content": "Later autosaved text"})
    assert updated.status_code == 200, updated.text
    assert client.get(path).json()["current_content"] == "Later autosaved text"
    historical = client.get(f"{path}/version/{number}")
    assert historical.status_code == 200, historical.text
    assert historical.json()["content"] == expected


def test_consecutive_autosaves_coalesce_without_changing_initial_version(api):
    from core.database import DocumentVersion

    client, factory, clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"
    first = client.put(path, json={"content": "First edit"})
    assert first.status_code == 200, first.text
    assert first.json()["version_count"] == 2
    _anchor_latest(factory, doc_id)

    second = client.put(path, json={"content": "Second edit"})
    assert second.status_code == 200, second.text
    assert second.json()["version_count"] == 2
    assert client.get(f"{path}/version/1").json()["content"] == "Original text"
    assert client.get(f"{path}/version/2").json()["content"] == "Second edit"
    with factory() as db:
        versions = db.query(DocumentVersion).filter(
            DocumentVersion.document_id == doc_id,
        ).order_by(DocumentVersion.version_number).all()
        assert [version.is_autosave for version in versions] == [False, True]
        assert [version.source for version in versions] == ["user", "user"]


def test_expired_autosave_creates_a_new_version(api):
    from datetime import timedelta

    client, factory, clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"
    first = client.put(path, json={"content": "First edit"})
    assert first.status_code == 200, first.text
    _anchor_latest(factory, doc_id, clock.current - timedelta(seconds=60))
    second = client.put(path, json={"content": "Later edit"})
    assert second.status_code == 200, second.text
    assert second.json()["version_count"] == 3
    assert client.get(f"{path}/version/2").json()["content"] == "First edit"


def test_forced_save_of_unchanged_content_is_a_protected_checkpoint(api):
    from core.database import DocumentVersion

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"
    first = client.put(path, json={"content": "First edit"})
    assert first.status_code == 200, first.text
    checkpoint = client.put(path, json={"content": "First edit", "force_version": True})
    assert checkpoint.status_code == 200, checkpoint.text
    assert checkpoint.json()["version_count"] == 3
    _anchor_latest(factory, doc_id)
    updated = client.put(path, json={"content": "Later edit"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version_count"] == 4
    assert client.get(f"{path}/version/3").json()["content"] == "First edit"
    with factory() as db:
        saved = db.query(DocumentVersion).filter(
            DocumentVersion.document_id == doc_id, DocumentVersion.version_number == 3,
        ).one()
        assert saved.is_autosave is False


def test_unchanged_autosave_does_not_create_a_version(api):
    client, _factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"
    saved = client.put(path, json={"content": "Original text"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["version_count"] == 1
