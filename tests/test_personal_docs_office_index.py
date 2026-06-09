from pathlib import Path

from src import personal_docs


def test_personal_index_includes_office_uploads(tmp_path, monkeypatch):
    docx_path = tmp_path / "report.docx"
    docx_path.write_bytes(b"PK fake docx bytes")

    monkeypatch.setattr(
        personal_docs,
        "extract_office_text",
        lambda path: "# Report\n\nreadable office text" if Path(path) == docx_path else "",
    )

    files = personal_docs.load_personal_index(str(tmp_path))

    assert [item["name"] for item in files] == ["report.docx"]
    assert files[0]["path"] == str(docx_path)
    assert files[0]["chunks"] == ["# Report\n\nreadable office text"]


def test_personal_index_default_extensions_advertise_office_support():
    for ext in (".docx", ".pptx", ".xlsx", ".xls"):
        assert ext in personal_docs.config.DEFAULT_EXTENSIONS
