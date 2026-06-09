import builtins

import pytest

from src.markitdown_runtime import (
    MARKITDOWN_MISSING,
    MARKITDOWN_EXTS,
    is_markitdown_format,
    load_markitdown,
    convert_to_markdown,
)


def _block_markitdown_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "markitdown":
            raise ImportError("No module named markitdown")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_missing_dependency_error_is_user_actionable(monkeypatch):
    _block_markitdown_import(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        load_markitdown()

    message = str(exc.value)
    assert message == MARKITDOWN_MISSING
    assert "requirements-optional.txt" in message


def test_convert_returns_none_when_dependency_missing(monkeypatch):
    _block_markitdown_import(monkeypatch)
    assert convert_to_markdown("whatever.docx") is None


def test_convert_returns_none_on_conversion_failure(monkeypatch):
    class Boom:
        def convert(self, path):
            raise ValueError("bad file")

    monkeypatch.setattr("src.markitdown_runtime.load_markitdown", lambda: Boom)
    assert convert_to_markdown("anything.docx") is None


def test_is_markitdown_format():
    assert is_markitdown_format("report.docx")
    assert is_markitdown_format("/path/to/Sheet.XLSX")  # case-insensitive
    assert not is_markitdown_format("notes.pdf")  # PDFs stay on pypdf
    assert not is_markitdown_format("readme.md")  # text stays on the text path


def test_markitdown_exts_cover_dropped_office_formats():
    for ext in (".docx", ".pptx", ".xlsx", ".xls"):
        assert ext in MARKITDOWN_EXTS


def test_convert_extracts_real_docx(tmp_path):
    """End-to-end: a .docx round-trips to Markdown with a heading (needs markitdown)."""
    pytest.importorskip("markitdown")
    Document = pytest.importorskip("docx").Document

    doc = Document()
    doc.add_heading("Quarterly Report", level=1)
    doc.add_paragraph("Revenue grew across all regions.")
    path = tmp_path / "report.docx"
    doc.save(str(path))

    md = convert_to_markdown(str(path))
    assert md and "Quarterly Report" in md
    assert "#" in md  # docx heading styles become Markdown headings
