"""Smoke test: PyMuPDF (fitz) renders pages and detects form fields."""
import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from src.pdf_runtime import load_pymupdf_for_pdf_viewer
from src.pdf_forms import has_form_fields


def test_load_pymupdf_returns_fitz_module():
    """load_pymupdf_for_pdf_viewer() must return the real fitz module."""
    mod = load_pymupdf_for_pdf_viewer()
    assert hasattr(mod, "open")
    assert hasattr(mod, "Matrix")


@pytest.mark.slow
def test_render_pdf_page_to_pixmap(tmp_path):
    """Create a one-page PDF in memory, render it to a PNG pixmap."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text((10, 50), "Odysseus smoke test", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    doc2 = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc2.page_count == 1
    mat = fitz.Matrix(1.5, 1.5)
    pix = doc2[0].get_pixmap(matrix=mat)
    assert pix.width > 0 and pix.height > 0
    assert pix.n >= 3
    doc2.close()


@pytest.mark.slow
def test_has_form_fields_false_for_plain_pdf(tmp_path):
    """has_form_fields() must return False for a PDF with no AcroForm widgets."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((10, 50), "No form here")
    path = str(tmp_path / "plain.pdf")
    doc.save(path)
    doc.close()

    assert has_form_fields(path) is False
