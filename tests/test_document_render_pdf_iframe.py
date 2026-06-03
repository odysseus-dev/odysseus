"""Regression tests for the document /render-pdf iframe path.

Two related bugs were fixed together:

  1. ``core/middleware.py:SecurityHeadersMiddleware`` was sending
     ``X-Frame-Options: DENY`` and ``Content-Security-Policy: ...;
     frame-ancestors 'none'`` on the ``/api/document/{doc_id}/render-pdf``
     response. The document library preview embeds the rendered PDF in an
     ``<iframe>`` (``static/js/documentLibrary.js``), so the browser blocked
     the load with ``ERR_BLOCKED_BY_RESPONSE`` and the user saw a blank
     panel. The fix extends the existing ``is_tool_render`` exemption to
     also cover ``/api/document/.../render-pdf`` — per-document auth still
     runs in the route handler.

  2. ``routes/document_routes.py:render_pdf`` calls ``fill_fields`` which
     calls ``src.pdf_forms._require_fitz``. When PyMuPDF is not installed
     that raises ``RuntimeError`` deep inside the route, bubbles out as a
     generic 500 with the cryptic "PDF render failed" message. The fix
     reuses the existing ``_load_pdf_viewer_fitz`` helper to fail fast with
     a clear 503 and a user-actionable install hint, matching the
     convention used by the other PDF endpoints.
"""

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
# Helpers — minimal fake request/response so we can drive dispatch directly.
# Drives the real middleware in isolation, no Starlette TestClient, no app
# boot, no auth — just the header logic.
# ---------------------------------------------------------------------------


class _FakeURL:
    def __init__(self, path: str):
        self.path = path


class _FakeRequest:
    def __init__(self, path: str):
        self.url = _FakeURL(path)
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


async def test_doc_render_pdf_is_iframeable():
    """Bug 1 fix: /api/document/{id}/render-pdf must NOT carry frame-blocking
    headers — the library preview embeds it in an iframe (see
    static/js/documentLibrary.js)."""
    resp = await _dispatch("/api/document/abc-123/render-pdf")

    assert "X-Frame-Options" not in resp.headers, (
        "render-pdf must not carry X-Frame-Options: DENY — the document "
        "library embeds it in an iframe."
    )
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors" not in csp, csp


async def test_doc_render_pdf_keeps_baseline_security_headers():
    """The exemption only relaxes framing. ``X-Content-Type-Options`` and
    ``Referrer-Policy`` are still set on every response (see the
    SecurityHeadersMiddleware contract) and must be preserved here."""
    resp = await _dispatch("/api/document/abc-123/render-pdf")

    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"


async def test_doc_export_pdf_still_frame_blocked():
    """export-pdf is a download (Content-Disposition: attachment), not an
    iframe embed. The exemption must NOT cover it — the path match has to
    be precise to avoid loosening the policy without benefit."""
    resp = await _dispatch("/api/document/abc-123/export-pdf")

    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")


async def test_doc_path_matching_is_precise():
    """Negative cases: paths that LOOK similar to render-pdf must NOT be
    exempted. Guards against future refactors that loosen the matcher.

    The matcher is the same startswith+endswith style the project already
    uses for is_tool_render / is_report, so the test pins that style.
    """
    for path in [
        "/api/document/abc-123/render-pdfx",         # extra suffix
        "/api/document/abc-123/render-pdf/foo",      # subpath
        "/api/documents/abc-123/render-pdf",         # wrong prefix (note plural)
    ]:
        resp = await _dispatch(path)
        assert resp.headers.get("X-Frame-Options") == "DENY", (
            f"Path {path!r} must keep the strict frame-blocking policy"
        )


async def test_tool_render_exemption_preserved():
    """Sanity check: the existing /api/tools/.../render exemption is not
    broken by the new /api/document/.../render-pdf exemption."""
    resp = await _dispatch("/api/tools/foo/bar/render")

    assert "X-Frame-Options" not in resp.headers
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors" not in csp


async def test_unrelated_paths_keep_strict_policy():
    """Other paths must keep the strict framing policy (no regression on
    the main change)."""
    resp = await _dispatch("/api/chat")

    assert resp.headers.get("X-Frame-Options") == "DENY"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp


# ---------------------------------------------------------------------------
# Test 2: render-pdf route must return 503 (not 500) when PyMuPDF is missing
# ---------------------------------------------------------------------------


_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
droutes.SessionLocal = _TS


def _req():
    """Minimal request stub: owner + auth_manager lookup path."""
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


def _make_pdf_doc() -> str:
    """Create a Document whose current_content carries a valid pdf_form_source
    front-matter pointer. The render-pdf handler reads this to look up the
    source upload — we only need a real marker to get past the 400 check."""
    content = (
        '<!-- pdf_form_source upload_id="'
        + "a" * 32  # matches UPLOAD_ID_RE (32 hex chars)
        + '" fields="3" -->\n'
        "- Field 1: value1\n- Field 2: value2\n- Field 3: value3\n"
    )
    db = _TS()
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


async def test_render_pdf_returns_503_when_pymupdf_missing(monkeypatch):
    """Bug 2 fix: missing PyMuPDF must surface as a clear 503, not a 500."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Stub the helpers the handler calls before the PyMuPDF gate so we
    # exercise the actual 503 path without a real uploaded PDF on disk.
    # - find_source_upload_id is imported lazily inside the handler — patch
    #   the source module.
    # - _resolve_user_upload_path is imported at module top level — patch
    #   via droutes.
    import src.pdf_form_doc as pdf_form_doc
    monkeypatch.setattr(pdf_form_doc, "find_source_upload_id", lambda _content: "a" * 32)
    monkeypatch.setattr(droutes, "_resolve_user_upload_path", lambda *a, **kw: "/tmp/fake.pdf")

    render_pdf = _endpoint("GET", "/api/document/{doc_id}/render-pdf", upload_handler=MagicMock())
    doc_id = _make_pdf_doc()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        await render_pdf(doc_id, _req())

    assert excinfo.value.status_code == 503, (
        f"Expected 503 with install hint, got {excinfo.value.status_code}: {excinfo.value.detail}"
    )
    detail = str(excinfo.value.detail)
    assert "requirements-optional.txt" in detail, detail
    assert "PyMuPDF" in detail, detail


async def test_render_pdf_503_runs_before_file_io(monkeypatch, tmp_path):
    """Stronger guarantee: the 503 is raised BEFORE we touch the source PDF
    path. If the route ever reordered the PyMuPDF check to happen after
    fill_fields, a missing PyMuPDF would still be a 500. This test pins
    the fail-fast ordering."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    sentinel_dir = tmp_path / "should-never-be-touched"
    sentinel_dir.mkdir()
    sentinel_path = str(sentinel_dir / "source.pdf")

    import src.pdf_form_doc as pdf_form_doc
    monkeypatch.setattr(pdf_form_doc, "find_source_upload_id", lambda _content: "a" * 32)
    # If the route opens the path before the PyMuPDF check, this will
    # raise FileNotFoundError and the test will fail with the wrong type.
    monkeypatch.setattr(droutes, "_resolve_user_upload_path", lambda *a, **kw: sentinel_path)

    render_pdf = _endpoint("GET", "/api/document/{doc_id}/render-pdf", upload_handler=MagicMock())
    doc_id = _make_pdf_doc()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        await render_pdf(doc_id, _req())

    assert excinfo.value.status_code == 503
