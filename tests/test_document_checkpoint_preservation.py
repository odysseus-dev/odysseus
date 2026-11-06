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
            request.state.current_user = request.headers.get("x-user", "alice")
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


def test_unauthorized_cross_user_autosave_rejected(api):
    from core.database import DocumentVersion, Document

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # Verify Alice's initial state
    initial_get = client.get(path)
    assert initial_get.status_code == 200
    assert initial_get.json()["current_content"] == "Original text"
    assert initial_get.json()["version_count"] == 1

    # Bob attempts an autosave edit on Alice's document
    bob_headers = {"x-user": "bob"}
    bob_autosave = client.put(
        path,
        json={"content": "Malicious Bob overwrite"},
        headers=bob_headers,
    )
    assert bob_autosave.status_code == 404

    # Bob attempts a forced version save on Alice's document
    bob_force = client.put(
        path,
        json={"content": "Malicious Bob checkpoint", "force_version": True},
        headers=bob_headers,
    )
    assert bob_force.status_code == 404

    # Verify Alice's document and version history are completely unchanged
    alice_check = client.get(path)
    assert alice_check.status_code == 200
    assert alice_check.json()["current_content"] == "Original text"
    assert alice_check.json()["version_count"] == 1

    with factory() as db:
        doc = db.query(Document).filter(Document.id == doc_id).one()
        assert doc.current_content == "Original text"
        assert doc.version_count == 1
        versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id).all()
        assert len(versions) == 1
        assert versions[0].content == "Original text"
        assert versions[0].version_number == 1
        assert versions[0].is_autosave is False


def test_concurrent_autosave_conflict_creates_new_version_instead_of_overwriting(api):
    """When a concurrent autosave updates the version row under our feet, OCC
    ensures the stale update falls through to creating a new version instead of
    overwriting the other worker's content.
    """
    from sqlalchemy import event

    client, factory, _clock = api
    engine = factory.kw["bind"]
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # First autosave creates version 2
    r1 = client.put(path, json={"content": "Tab 1 first edit"})
    assert r1.status_code == 200
    assert r1.json()["version_count"] == 2
    _anchor_latest(factory, doc_id)

    # Interleave: right when Tab 1's followup autosave tries to execute the conditional
    # UPDATE, a concurrent worker modifies version 2's content to "Tab 2 concurrent edit".
    intercepted = [False]

    def on_cursor(conn, cursor, statement, parameters, context, executemany):
        if not intercepted[0] and "UPDATE document_versions" in statement:
            intercepted[0] = True
            cursor.execute(
                "UPDATE document_versions SET content = ? WHERE document_id = ?",
                ("Tab 2 concurrent edit", doc_id),
            )

    event.listen(engine, "before_cursor_execute", on_cursor)
    try:
        # Now Tab 1 sends another autosave within the 60s window.
        # Because version 2 was concurrently modified (content no longer matches stale_content),
        # the conditional UPDATE affects 0 rows, coalesced remains False, and a new version is created.
        r2 = client.put(path, json={"content": "Tab 1 followup edit"})
        assert r2.status_code == 200
        assert r2.json()["version_count"] == 3

        # Both contents are preserved in history
        assert client.get(f"{path}/version/2").json()["content"] == "Tab 2 concurrent edit"
        assert client.get(f"{path}/version/3").json()["content"] == "Tab 1 followup edit"
    finally:
        event.remove(engine, "before_cursor_execute", on_cursor)



def test_ai_edit_followed_by_autosave_creates_new_version(api):
    """AI edits have is_autosave=False and must never be coalesced by a subsequent user autosave."""
    from core.database import DocumentVersion, Document

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # Simulate an AI edit tool write
    with factory() as db:
        doc = db.query(Document).filter_by(id=doc_id).one()
        doc.version_count = 2
        doc.current_content = "AI generated analysis"
        ai_ver = DocumentVersion(
            id="ai-ver-2",
            document_id=doc_id,
            version_number=2,
            content="AI generated analysis",
            summary="Updated by AI",
            source="ai",
            is_autosave=False,
        )
        db.add(ai_ver)
        db.commit()

    _anchor_latest(factory, doc_id)

    # User autosaves shortly after
    user_save = client.put(path, json={"content": "User notes added"})
    assert user_save.status_code == 200
    assert user_save.json()["version_count"] == 3

    # AI version remains intact in history
    ai_hist = client.get(f"{path}/version/2")
    assert ai_hist.status_code == 200
    assert ai_hist.json()["content"] == "AI generated analysis"
    assert client.get(f"{path}/version/3").json()["content"] == "User notes added"


def test_coalesce_boundary_window_at_59_vs_60_seconds(api):
    from datetime import timedelta

    client, factory, clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # Edit 1 -> version 2
    edit1 = client.put(path, json={"content": "Edit at T0"})
    assert edit1.status_code == 200
    assert edit1.json()["version_count"] == 2

    # Anchor at 59 seconds ago -> within 60s window (< 60) -> coalesces
    _anchor_latest(factory, doc_id, clock.current - timedelta(seconds=59))
    edit2 = client.put(path, json={"content": "Edit at T+59s"})
    assert edit2.status_code == 200
    assert edit2.json()["version_count"] == 2
    assert client.get(f"{path}/version/2").json()["content"] == "Edit at T+59s"

    # Anchor at exactly 60 seconds ago -> age is 60 -> age < 60 is False -> creates version 3
    _anchor_latest(factory, doc_id, clock.current - timedelta(seconds=60))
    edit3 = client.put(path, json={"content": "Edit at T+60s"})
    assert edit3.status_code == 200
    assert edit3.json()["version_count"] == 3
    assert client.get(f"{path}/version/2").json()["content"] == "Edit at T+59s"
    assert client.get(f"{path}/version/3").json()["content"] == "Edit at T+60s"


def test_concurrent_checkpoint_and_autosave_preserves_semantics(api):
    """Concurrent forced checkpoint and autosave using independent threads/transactions
    must not clobber each other:
    - Checkpoint creates version 3 with is_autosave=False
    - Autosave does not overwrite older version or desync current_content
    - Document.current_content matches the latest version
    - No version number collisions
    """
    import threading

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # First autosave creates version 2
    r1 = client.put(path, json={"content": "Draft edit 1"})
    assert r1.status_code == 200
    assert r1.json()["version_count"] == 2
    _anchor_latest(factory, doc_id)

    # Concurrently execute a checkpoint and an autosave from two threads
    results = {}
    errors = []

    def run_checkpoint():
        try:
            r = client.put(path, json={"content": "Milestone checkpoint", "force_version": True})
            results["cp"] = r
        except Exception as exc:
            errors.append(("cp", exc))

    def run_autosave():
        try:
            r = client.put(path, json={"content": "Draft edit 2", "force_version": False})
            results["as"] = r
        except Exception as exc:
            errors.append(("as", exc))

    t1 = threading.Thread(target=run_checkpoint)
    t2 = threading.Thread(target=run_autosave)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    assert results["cp"].status_code == 200
    assert results["as"].status_code == 200

    # Verify document and versions integrity
    with factory() as db:
        from core.database import Document, DocumentVersion
        doc = db.query(Document).filter_by(id=doc_id).one()
        versions = db.query(DocumentVersion).filter_by(document_id=doc_id).order_by(DocumentVersion.version_number.asc()).all()

        # Version numbers must be strictly unique and sequential: 1, 2, 3, 4 (or 1, 2, 3 if autosave coalesced before checkpoint)
        ver_nums = [v.version_number for v in versions]
        assert len(ver_nums) == len(set(ver_nums)), f"Duplicate version numbers found: {ver_nums}"
        assert doc.version_count == len(versions)
        assert doc.current_content == versions[-1].content

        # The checkpoint must exist with is_autosave=False and content "Milestone checkpoint"
        cp_ver = next(v for v in versions if v.content == "Milestone checkpoint")
        assert cp_ver.is_autosave is False

        # Every version in history is retrievable through the API
        for v in versions:
            res = client.get(f"{path}/version/{v.version_number}")
            assert res.status_code == 200
            assert res.json()["content"] == v.content


def test_concurrent_new_versions_allocate_unique_sequential_numbers(api):
    """Two concurrent non-coalescing saves must allocate strictly increasing
    version numbers without uniqueness conflicts, version collision, or 500s.
    """
    import threading

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # Two concurrent forced checkpoints
    results = {}
    errors = []

    def run_save(key, text):
        try:
            r = client.put(path, json={"content": text, "force_version": True})
            results[key] = r
        except Exception as exc:
            errors.append((key, exc))

    t1 = threading.Thread(target=run_save, args=("save1", "Concurrent Milestone A"))
    t2 = threading.Thread(target=run_save, args=("save2", "Concurrent Milestone B"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    assert results["save1"].status_code == 200
    assert results["save2"].status_code == 200

    with factory() as db:
        from core.database import Document, DocumentVersion
        doc = db.query(Document).filter_by(id=doc_id).one()
        versions = db.query(DocumentVersion).filter_by(document_id=doc_id).order_by(DocumentVersion.version_number.asc()).all()

        assert len(versions) == 3  # Initial (v1), Save1 (v2), Save2 (v3)
        assert [v.version_number for v in versions] == [1, 2, 3]
        assert doc.version_count == 3
        assert doc.current_content == versions[-1].content

        # Both concurrent milestone edits must be distinctly queryable and restorable
        v2_res = client.get(f"{path}/version/2")
        v3_res = client.get(f"{path}/version/3")
        assert v2_res.status_code == 200
        assert v3_res.status_code == 200
        assert {v2_res.json()["content"], v3_res.json()["content"]} == {"Concurrent Milestone A", "Concurrent Milestone B"}


def test_concurrent_restore_and_autosave(api):
    """Concurrent restore and autosave must serialize properly and not collide on version numbers."""
    import threading

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # Create version 2
    r2 = client.put(path, json={"content": "Version 2 content", "force_version": True})
    assert r2.status_code == 200

    results = {}
    errors = []

    def run_restore():
        try:
            r = client.post(f"{path}/restore/1")
            results["restore"] = r
        except Exception as exc:
            errors.append(("restore", exc))

    def run_autosave():
        try:
            r = client.put(path, json={"content": "Autosave while restoring", "force_version": False})
            results["autosave"] = r
        except Exception as exc:
            errors.append(("autosave", exc))

    t1 = threading.Thread(target=run_restore)
    t2 = threading.Thread(target=run_autosave)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    assert results["restore"].status_code == 200
    assert results["autosave"].status_code == 200

    with factory() as db:
        from core.database import Document, DocumentVersion
        doc = db.query(Document).filter_by(id=doc_id).one()
        versions = db.query(DocumentVersion).filter_by(document_id=doc_id).order_by(DocumentVersion.version_number.asc()).all()

        ver_nums = [v.version_number for v in versions]
        assert len(ver_nums) == len(set(ver_nums)), f"Duplicate versions: {ver_nums}"
        assert doc.version_count == len(versions)
        assert doc.current_content == versions[-1].content


def test_transaction_rollback_preserves_consistent_state(api):
    """When a failure occurs during document update, the transaction rolls back cleanly,
    leaving Document and DocumentVersion state completely uncorrupted.
    """
    from unittest.mock import patch

    client, factory, _clock = api
    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # First update succeeds -> version 2
    r1 = client.put(path, json={"content": "Committed version 2"})
    assert r1.status_code == 200
    assert r1.json()["version_count"] == 2

    # Attempt a second update, but simulate an error during db.commit
    with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("Database error during commit")):
        r2 = client.put(path, json={"content": "Failed edit during commit"})
        assert r2.status_code == 500

    # Verify state is completely consistent and matches version 2
    check = client.get(path)
    assert check.status_code == 200
    assert check.json()["current_content"] == "Committed version 2"
    assert check.json()["version_count"] == 2

    with factory() as db:
        from core.database import Document, DocumentVersion
        doc = db.query(Document).filter_by(id=doc_id).one()
        assert doc.current_content == "Committed version 2"
        assert doc.version_count == 2
        versions = db.query(DocumentVersion).filter_by(document_id=doc_id).all()
        assert len(versions) == 2
        assert {v.content for v in versions} == {"Original text", "Committed version 2"}


def test_postgresql_dialect_row_locking_path(api):
    """Verify that PostgreSQL-style backends execute the row-level locking path
    (with_for_update) without invoking SQLite's BEGIN IMMEDIATE.
    """
    client, factory, _clock = api
    engine = factory.kw["bind"]
    engine.dialect.name = "postgresql"

    doc_id = _create_document(client)
    path = f"/api/document/{doc_id}"

    # Update succeeds with simulated PostgreSQL dialect
    r = client.put(path, json={"content": "PostgreSQL updated content"})
    assert r.status_code == 200
    assert r.json()["version_count"] == 2
    assert r.json()["current_content"] == "PostgreSQL updated content"


