"""Tests for src.document_processor._process_pdf — text extraction + OCR fallback.

These cover the chat-side PDF path (POST /api/documents/import-pdf →
_process_pdf). The document-viewer path (render-pages) is a separate
PyMuPDF-only surface tested in test_pdf_runtime.py.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


PDF_PATH = "/tmp/fake.pdf"
OCR_TEXT = "scanned-page-marker-text"


def _fake_page(*, text: str = "", images=None):
    page = MagicMock()
    page.extract_text.return_value = text
    if images is None:
        page.images = []
    else:
        page.images = images
    return page


def _fake_pypdf_reader(pages):
    reader = MagicMock()
    reader.pages = pages
    return reader


def _fake_fitz_module(pages=None):
    mod = MagicMock()
    mod.Matrix = MagicMock(return_value="matrix")
    pdf_doc = MagicMock()
    pdf_doc.__iter__ = MagicMock(return_value=iter(pages or []))
    pdf_doc.__getitem__ = MagicMock(side_effect=lambda i: (pages or [MagicMock()])[i])
    pdf_doc.close = MagicMock()
    mod.open.return_value = pdf_doc
    return mod


def _fake_page_obj(raise_on_get_pixmap=None):
    page = MagicMock()
    if raise_on_get_pixmap is not None:
        page.get_pixmap.side_effect = raise_on_get_pixmap
    else:
        fake_pix = MagicMock()

        def _fake_save_pix(path):
            with open(path, "wb") as f:
                f.write(b"PNG")

        fake_pix.save.side_effect = _fake_save_pix
        page.get_pixmap.return_value = fake_pix
    return page


def test_full_page_ocr_fires_when_pypdf_yields_no_text_and_no_images():
    """Scanned-PDF symptom: pypdf returns no text and no images per page.

    The function must render the page with PyMuPDF and ask the VL model,
    so the chat gets readable content instead of
    'PDF processed but no readable content found'.
    """
    fake_pix = MagicMock()
    page_obj = MagicMock()
    page_obj.get_pixmap.return_value = fake_pix

    def _fake_save_pix(path):
        with open(path, "wb") as f:
            f.write(b"PNG")

    fake_pix.save.side_effect = _fake_save_pix

    fake_fitz = _fake_fitz_module(pages=[page_obj])
    fake_reader = _fake_pypdf_reader([_fake_page(text="", images=[])])

    with patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", return_value=fake_fitz), \
         patch("src.document_processor.analyze_image_with_vl", return_value=OCR_TEXT) as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader):
        from src.document_processor import _process_pdf
        result = _process_pdf(PDF_PATH)

    assert OCR_TEXT in result, f"OCR text missing from result: {result!r}"
    assert vl.call_count == 1, f"VL should be called once per text-less page, got {vl.call_count}"
    assert "PDF processed but no readable content found" not in result


def test_pypdf_text_path_skips_pymupdf():
    """Fast path: when pypdf returns text for a page, do not load PyMuPDF.

    The current code preserves this; the new full-page fallback must
    not turn every chat into a PyMuPDF render + VL call. Patch the
    PyMuPDF loader to raise so the fallback branch is guaranteed not
    to run regardless of whether the test environment has PyMuPDF.
    """
    fake_reader = _fake_pypdf_reader([_fake_page(text="hello world", images=[])])

    def _no_pymupdf():
        from src.pdf_runtime import PDF_VIEWER_PYMUPDF_MISSING
        raise RuntimeError(PDF_VIEWER_PYMUPDF_MISSING)

    with patch("src.document_processor.analyze_image_with_vl") as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader), \
         patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", side_effect=_no_pymupdf):
        from src.document_processor import _process_pdf
        result = _process_pdf(PDF_PATH)

    assert "hello world" in result
    assert vl.call_count == 0, "VL must not be called when pypdf returned text"


def test_full_page_ocr_degrades_gracefully_without_pymupdf():
    """If PyMuPDF is missing, the chat path must not crash — fall back to
    the existing 'no readable content' message (or the per-image path).
    This matches the viewer error's contract: PyMuPDF is optional.
    """
    fake_reader = _fake_pypdf_reader([_fake_page(text="", images=[])])

    def _raise_missing():
        from src.pdf_runtime import PDF_VIEWER_PYMUPDF_MISSING
        raise RuntimeError(PDF_VIEWER_PYMUPDF_MISSING)

    with patch("src.document_processor.analyze_image_with_vl"), \
         patch("pypdf.PdfReader", return_value=fake_reader), \
         patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", side_effect=_raise_missing):
        from src.document_processor import _process_pdf
        result = _process_pdf(PDF_PATH)

    assert "PDF processed but no readable content found" in result


def test_full_page_ocr_cap_counts_attempted_pages_not_successful():
    """The page cap must bound work, not just successes.

    If OCR returns empty/'unavailable' for every page, the cap on
    successful pages is never hit and the loop walks the whole PDF —
    blowing latency and VL cost. Increment the counter per attempted
    page instead.
    """
    from src.document_processor import _PDF_FULLPAGE_OCR_PAGE_CAP

    many_pages = [_fake_page_obj() for _ in range(_PDF_FULLPAGE_OCR_PAGE_CAP + 5)]
    fake_fitz = _fake_fitz_module(pages=many_pages)
    fake_reader = _fake_pypdf_reader([_fake_page(text="", images=[])])

    with patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", return_value=fake_fitz), \
         patch("src.document_processor.analyze_image_with_vl", return_value="[VL model unavailable]") as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader):
        from src.document_processor import _process_pdf
        _process_pdf(PDF_PATH)

    assert vl.call_count == _PDF_FULLPAGE_OCR_PAGE_CAP, (
        f"cap should bound attempted pages, got {vl.call_count} calls "
        f"(cap={_PDF_FULLPAGE_OCR_PAGE_CAP})"
    )


def test_full_page_ocr_continues_after_per_page_exception():
    """A bad page (get_pixmap/save/analyze throws) must not abort the loop.

    The current outer try/except around fitz.open swallows the error
    but it also short-circuits the rest of the pages. The per-page
    body needs its own try/except so one bad page doesn't lose the
    remaining OCR pages.
    """
    bad_page = _fake_page_obj(raise_on_get_pixmap=RuntimeError("corrupt page"))
    good_page = _fake_page_obj()
    fake_fitz = _fake_fitz_module(pages=[bad_page, good_page])
    fake_reader = _fake_pypdf_reader([_fake_page(text="", images=[])])

    vl_calls = []

    def _fake_vl(path, owner=None):
        vl_calls.append(path)
        return OCR_TEXT

    with patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", return_value=fake_fitz), \
         patch("src.document_processor.analyze_image_with_vl", side_effect=_fake_vl) as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader):
        from src.document_processor import _process_pdf
        result = _process_pdf(PDF_PATH)

    assert vl.call_count == 1, (
        f"VL should be called once for the good page after the bad page aborts render, got {vl.call_count}"
    )
    assert OCR_TEXT in result, "OCR text from the good page should still make it into the result"


def test_full_page_ocr_forwards_owner_to_vl():
    """The owner parameter must reach the VL call so multi-tenant
    setups resolve the correct endpoint credentials.
    """
    fake_pix = MagicMock()
    page_obj = MagicMock()
    page_obj.get_pixmap.return_value = fake_pix

    def _fake_save_pix(path):
        with open(path, "wb") as f:
            f.write(b"PNG")

    fake_pix.save.side_effect = _fake_save_pix

    fake_fitz = _fake_fitz_module(pages=[page_obj])
    fake_reader = _fake_pypdf_reader([_fake_page(text="", images=[])])

    with patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", return_value=fake_fitz), \
         patch("src.document_processor.analyze_image_with_vl", return_value=OCR_TEXT) as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader):
        from src.document_processor import _process_pdf
        _process_pdf(PDF_PATH, owner="tenant-42")

    assert vl.call_count == 1
    _, kwargs = vl.call_args
    assert kwargs.get("owner") == "tenant-42", (
        f"VL call must forward owner, got kwargs={kwargs}"
    )
