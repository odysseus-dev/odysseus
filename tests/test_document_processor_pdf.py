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


def test_full_page_ocr_fires_when_pypdf_yields_no_text_and_no_images(monkeypatch):
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

    with patch.dict(sys.modules, {"fitz": fake_fitz}), \
         patch("src.document_processor.analyze_image_with_vl", return_value=OCR_TEXT) as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader):
        from src.document_processor import _process_pdf
        result = _process_pdf(PDF_PATH)

    assert OCR_TEXT in result, f"OCR text missing from result: {result!r}"
    assert vl.call_count == 1, f"VL should be called once per text-less page, got {vl.call_count}"
    assert "PDF processed but no readable content found" not in result


def test_pypdf_text_path_skips_pymupdf(monkeypatch):
    """Fast path: when pypdf returns text for a page, do not load PyMuPDF.

    The current code preserves this; the new full-page fallback must
    not turn every chat into a PyMuPDF render + VL call.
    """
    fake_reader = _fake_pypdf_reader([_fake_page(text="hello world", images=[])])

    with patch("src.document_processor.analyze_image_with_vl") as vl, \
         patch("pypdf.PdfReader", return_value=fake_reader):
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
         patch("src.pdf_runtime.load_pymupdf_for_pdf_viewer", _raise_missing):
        from src.document_processor import _process_pdf
        result = _process_pdf(PDF_PATH)

    assert "PDF processed but no readable content found" in result
