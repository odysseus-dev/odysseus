"""Regression tests for the document PDF preview framing headers and PyMuPDF dependency handling."""

import builtins
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.document_routes as droutes
from core.database import Document
from core.middleware import SecurityHeadersMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeURL:
    def __init__(self, path: str):
        self.path = path
        self.scheme = "http"


class _FakeRequest:
    def __init__(self, path: str):
        self.url = _FakeURL(path)
        self.headers = {}
        self.state = SimpleNamespace()


class _FakeResponse:
    def __init__(self):
        self.headers: dict[str, str] = {}


async def _dispatch(path: str) -> _FakeResponse:
    mw = SecurityHeadersMiddleware(MagicMock())
    resp = _FakeResponse()
    call_next = AsyncMock(return_value=resp)
    await mw.dispatch(_FakeRequest(path), call_next)
    return resp


# ---------------------------------------------------------------------------
# Test 1: middleware framing policy on /api/document/.../render-pdf
# ---------------------------------------------------------------------------


async def test_doc_render_pdf_same_origin_framing():
    """Assert that /api/document/{id}/render-pdf allows same-origin framing."""
    resp = await _dispatch("/api/document/abc-123/render-pdf")

    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'self'" in csp


async def test_doc_render_pdf_keeps_baseline_security_headers():
    """Assert that baseline security headers are preserved on the render-pdf path."""
    resp = await _dispatch("/api/document/abc-123/render-pdf")

    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"


async def test_doc_export_pdf_still_frame_blocked():
    """Assert that the export-pdf path remains frame-blocked."""
    resp = await _dispatch("/api/document/abc-123/export-pdf")

    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")


async def test_doc_path_matching_is_precise():
    """Assert that similar paths are not exempted from framing restrictions."""
    for path in [
        "/api/document/abc-123/render-pdfx",
        "/api/document/abc-123/render-pdf/foo",
        "/api/documents/abc-123/render-pdf",
    ]:
        resp = await _dispatch(path)
        assert resp.headers.get("X-Frame-Options") == "DENY"


async def test_tool_render_exemption_preserved():
    """Assert that the tool-render path remains exempt from framing headers."""
    resp = await _dispatch("/api/tools/foo/bar/render")

    assert "X-Frame-Options" not in resp.headers
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors" not in csp


async def test_unrelated_paths_keep_strict_policy():
    """Assert that other paths keep the strict framing policy."""
    resp = await _dispatch("/api/chat")

    assert resp.headers.get("X-Frame-Options") == "DENY"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp


# ---------------------------------------------------------------------------
# Test 2: render-pdf route must return 503 (not 500) when PyMuPDF is missing
# ---------------------------------------------------------------------------


@pytest.fixture
def test_db(monkeypatch):
    """Create a temporary SQLite database and patch routes.document_routes.SessionLocal."""
    import os
    tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmpdb.close()
    engine = create_engine(
        f"sqlite:///{tmpdb.name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    ts = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(droutes, "SessionLocal", ts)
    try:
        yield ts
    finally:
        engine.dispose()
        try:
            os.unlink(tmpdb.name)
        except OSError:
            pass


def _req():
    """Minimal request stub."""
    return SimpleNamespace(
        state=SimpleNamespace(current_user="tester"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )


def _endpoint(method: str, path: str, upload_handler=None):
    router = droutes.setup_document_routes(MagicMock(), upload_handler)
    for r in router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _make_pdf_doc(db_session) -> str:
    """Create a test Document with a pdf_form_source front-matter pointer."""
    content = (
        '<!-- pdf_form_source upload_id="'
        + "a" * 32
        + '" fields="3" -->\n'
        "- Field 1: value1\n- Field 2: value2\n- Field 3: value3\n"
    )
    db = db_session()
    try:
        doc = Document(
            id=str(uuid.uuid4()),
            session_id=None,
            title="t",
            language="markdown",
            current_content=content,
            version_count=1,
            is_active=True,
            owner="tester",
        )
        db.add(doc)
        db.commit()
        return doc.id
    finally:
        db.close()


async def test_render_pdf_returns_503_when_pymupdf_missing(monkeypatch, test_db):
    """Assert that the render-pdf path returns 503 when PyMuPDF is not installed."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Stub route dependencies to isolate the PyMuPDF check
    import src.pdf_form_doc as pdf_form_doc
    monkeypatch.setattr(pdf_form_doc, "find_source_upload_id", lambda _content: "a" * 32)
    monkeypatch.setattr(droutes, "_resolve_user_upload_path", lambda *a, **kw: "/tmp/fake.pdf")

    render_pdf = _endpoint("GET", "/api/document/{doc_id}/render-pdf", upload_handler=MagicMock())
    doc_id = _make_pdf_doc(test_db)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        await render_pdf(doc_id, _req())

    assert excinfo.value.status_code == 503
    detail = str(excinfo.value.detail)
    assert "requirements-optional.txt" in detail
    assert "PyMuPDF" in detail


async def test_render_pdf_503_runs_before_file_io(monkeypatch, test_db, tmp_path):
    """Assert that the PyMuPDF check runs before resolving or checking the source file path."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Use a non-existent path to verify the check fails before checking path existence
    sentinel_dir = tmp_path / "should-never-be-touched"
    sentinel_dir.mkdir()
    sentinel_path = str(sentinel_dir / "source.pdf")

    import src.pdf_form_doc as pdf_form_doc
    monkeypatch.setattr(pdf_form_doc, "find_source_upload_id", lambda _content: "a" * 32)
    monkeypatch.setattr(droutes, "_resolve_user_upload_path", lambda *a, **kw: sentinel_path)

    render_pdf = _endpoint("GET", "/api/document/{doc_id}/render-pdf", upload_handler=MagicMock())
    doc_id = _make_pdf_doc(test_db)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        await render_pdf(doc_id, _req())

    assert excinfo.value.status_code == 503
