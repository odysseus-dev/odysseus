from pathlib import Path

from src import personal_docs


def test_personal_index_includes_pdf_uploads(tmp_path, monkeypatch):
    pdf_path = tmp_path / "notes.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake test pdf")

    monkeypatch.setattr(
        personal_docs,
        "extract_pdf_text",
        lambda path: "readable pdf text" if Path(path) == pdf_path else "",
    )

    files = personal_docs.load_personal_index(str(tmp_path))

    assert [item["name"] for item in files] == ["notes.pdf"]
    assert files[0]["path"] == str(pdf_path)
    assert files[0]["chunks"] == ["readable pdf text"]


def test_personal_index_default_extensions_advertise_pdf_support():
    assert ".pdf" in personal_docs.config.DEFAULT_EXTENSIONS
