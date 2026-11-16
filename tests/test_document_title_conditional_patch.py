"""Deterministic tests for conditional document title PATCH requests (NB-F1-Server).

Tests the atomic persistence boundary for automatic titles, ensuring that:
1. Automatic titles (if_untitled=True) can be applied to an untitled document.
2. Manual titles replace automatic titles unconditionally.
3. Automatic titles with if_untitled=True are rejected with HTTP 409 if a manual title was committed.
4. Older automatic requests completing after a newer manual update fail with HTTP 409 and do not overwrite the manual title.
5. Concurrent requests across separate database sessions cannot race to overwrite committed manual titles.
6. Backward compatibility is preserved for existing PATCH callers (if_untitled omitted/False).
"""

import sys
import uuid
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.helpers.database import disposable_database
from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import routes.document_routes as droutes
from core.database import Document
from routes.document_helpers import DocumentPatch


@pytest.fixture(autouse=True)
def _document_database(tmp_path):
    with disposable_database(tmp_path) as factory:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(sys.modules[__name__], "_TS", factory, raising=False)
            patcher.setattr(droutes, "SessionLocal", factory)
            yield


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    router = droutes.setup_document_routes(MagicMock(), None)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _create_doc(title="Untitled", user="alice"):
    doc_id = str(uuid.uuid4())
    db = _TS()
    try:
        doc = Document(
            id=doc_id,
            owner=user,
            title=title,
            current_content="# Initial Content",
            version_count=1,
            is_active=True,
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()
    return doc_id


def test_automatic_title_applied_to_untitled_document():
    """Automatic title with if_untitled=True succeeds when document is Untitled."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Untitled")

    res = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Derived Auto Title", if_untitled=True)))
    assert res["title"] == "Derived Auto Title"

    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        assert row.title == "Derived Auto Title"
    finally:
        db.close()


def test_manual_title_replaces_automatic_title():
    """Manual title update (if_untitled=False/omitted) unconditionally overwrites any title."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Auto Title")

    res = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="User Manual Title")))
    assert res["title"] == "User Manual Title"

    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        assert row.title == "User Manual Title"
    finally:
        db.close()


def test_automatic_title_rejected_after_manual_title_committed():
    """Automatic title with if_untitled=True raises HTTP 409 if document has a committed non-Untitled title."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Committed Manual Title")

    with pytest.raises(HTTPException) as exc_info:
        asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Stale Auto Title", if_untitled=True)))

    assert exc_info.value.status_code == 409
    assert "not untitled" in exc_info.value.detail.lower()

    # Verify the database title was NOT overwritten
    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        assert row.title == "Committed Manual Title"
    finally:
        db.close()


def test_older_automatic_request_completing_after_newer_manual_update():
    """Simulate an in-flight auto-title request whose commit arrives after a manual update."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Untitled")

    # 1. User performs manual edit
    res_manual = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Newer Manual Title")))
    assert res_manual["title"] == "Newer Manual Title"

    # 2. Older delayed auto-title request arrives
    with pytest.raises(HTTPException) as exc_info:
        asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Older Delayed Auto Title", if_untitled=True)))

    assert exc_info.value.status_code == 409

    # 3. Verify manual title is preserved in DB
    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        assert row.title == "Newer Manual Title"
    finally:
        db.close()


def test_concurrent_requests_separate_sessions_atomic():
    """Two concurrent requests using separate DB sessions: an auto-title and a manual title.
    Regardless of timing, the manual title must be preserved atomically."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Untitled")

    results = []
    errors = []

    def run_auto():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            r = loop.run_until_complete(patch_ep(_req(), doc_id, DocumentPatch(title="Auto Title", if_untitled=True)))
            results.append(("auto", r["title"]))
        except HTTPException as e:
            errors.append(("auto", e.status_code))
        finally:
            loop.close()

    def run_manual():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            r = loop.run_until_complete(patch_ep(_req(), doc_id, DocumentPatch(title="Manual Title")))
            results.append(("manual", r["title"]))
        except HTTPException as e:
            errors.append(("manual", e.status_code))
        finally:
            loop.close()

    t_manual = threading.Thread(target=run_manual)
    t_auto = threading.Thread(target=run_auto)

    # Start manual first, then auto
    t_manual.start()
    t_auto.start()

    t_manual.join()
    t_auto.join()

    # Check database
    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        # The committed title must NEVER be the auto title if manual completed after or before
        # If manual won, title is "Manual Title"
        assert row.title == "Manual Title"
    finally:
        db.close()


def test_backward_compatibility_existing_patch_clients():
    """Existing clients sending language or session_id or title without if_untitled succeed normally."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Existing Title")

    # 1. Update language without title
    res_lang = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(language="python")))
    assert res_lang["language"] == "python"
    assert res_lang["title"] == "Existing Title"

    # 2. Unconditional title update with legacy payload
    res_legacy = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Updated Legacy Title")))
    assert res_legacy["title"] == "Updated Legacy Title"

    # 3. Explicit expected_title matching succeeds
    res_expected = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Expected Success", expected_title="Updated Legacy Title")))
    assert res_expected["title"] == "Expected Success"

    # 4. Explicit expected_title mismatch fails with 409
    with pytest.raises(HTTPException) as exc_info:
        asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Mismatch Fail", expected_title="Wrong Title")))
    assert exc_info.value.status_code == 409


def test_clearing_title_persists_empty_string_and_enables_auto_title():
    """Clearing title to empty string persists and subsequently allows if_untitled auto-title."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Initial Title")

    # 1. User clears title to empty string
    res_clear = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="")))
    assert res_clear["title"] == ""

    # Verify DB has empty string
    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        assert row.title == ""
    finally:
        db.close()

    # 2. Auto-title arrives with if_untitled=True and successfully applies
    res_auto = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="Derived Title", if_untitled=True)))
    assert res_auto["title"] == "Derived Title"

    db = _TS()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        assert row.title == "Derived Title"
    finally:
        db.close()


def test_expected_title_supports_empty_and_untitled_equivalency():
    """expected_title allows empty string and Untitled interchangeably for untitled documents."""
    patch_ep = _endpoint("PATCH", "/api/document/{doc_id}")
    doc_id = _create_doc(title="Untitled")

    res = asyncio_run(patch_ep(_req(), doc_id, DocumentPatch(title="First Named", expected_title="")))
    assert res["title"] == "First Named"


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
