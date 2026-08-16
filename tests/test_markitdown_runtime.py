import builtins
import zipfile

import pytest

from src.markitdown_runtime import (
    MARKITDOWN_MISSING,
    MARKITDOWN_EXTS,
    is_markitdown_format,
    load_markitdown,
    convert_to_markdown,
    _extract_docx_native,
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


def _make_docx(tmp_path, xml_content: str) -> str:
    """Build a minimal .docx (zip with ``word/document.xml``) for parser tests."""
    path = tmp_path / "payload.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml_content)
    return str(path)


def test_native_extract_valid_docx(tmp_path):
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>"
    )
    out = _extract_docx_native(_make_docx(tmp_path, xml))
    assert out == "Hello"


def test_native_extract_rejects_doctype_external_entity(tmp_path):
    """A DOCTYPE with an external entity must be rejected, not expanded (XXE)."""
    xml = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>"
    )
    assert _extract_docx_native(_make_docx(tmp_path, xml)) is None


def test_native_extract_rejects_doctype_billion_laughs(tmp_path):
    """Internal-entity expansion ('billion laughs') must be rejected, not parsed."""
    xml = (
        '<?xml version="1.0"?>'
        "<!DOCTYPE lolz ["
        '<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        "]>"
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>safe</w:t></w:r></w:p></w:body></w:document>"
    )
    assert _extract_docx_native(_make_docx(tmp_path, xml)) is None


def test_native_extract_case_insensitive_doctype(tmp_path):
    """Lowercase '<!doctype' is still rejected (case-insensitive check)."""
    xml = (
        '<?xml version="1.0"?><!doctype r [<!ENTITY x SYSTEM "http://evil/xxe">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>body text</w:t></w:r></w:p></w:body></w:document>"
    )
    assert _extract_docx_native(_make_docx(tmp_path, xml)) is None
