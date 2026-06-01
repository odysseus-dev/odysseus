import builtins

import pytest

from src.pdf_runtime import PDF_VIEWER_PYMUPDF_MISSING, load_pymupdf_for_pdf_viewer


def test_pdf_viewer_dependency_error_is_user_actionable(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named fitz")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError) as exc:
        load_pymupdf_for_pdf_viewer()

    message = str(exc.value)
    assert message == PDF_VIEWER_PYMUPDF_MISSING
    assert "requirements-optional.txt" in message
    assert "PyMuPDF" in message
