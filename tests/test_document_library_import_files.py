"""Regression coverage for document-library device imports.

The browser module depends on DOM-only imports, so these tests pin the source
wiring that caused opaque "Imported 0 files" failures: import POSTs should use
the same authenticated request style as the rest of the document library and
surface backend error details instead of collapsing to a generic message.
"""

from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "static/js/documentLibrary.js"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def test_library_import_posts_are_authenticated_and_report_backend_errors():
    body = _between(_src(), "async function libraryImportFiles(fileList)", "export function openLibrary")

    assert body.count("credentials: 'same-origin'") >= 3
    assert "throw await importRequestError(res, 'PDF import failed')" in body
    assert "throw await importRequestError(res, `Spreadsheet import failed for ${sheetTitle}`)" in body
    assert "throw await importRequestError(res, `Import failed for ${name}`)" in body
    assert "throw new Error('Server error')" not in body


def test_spreadsheet_import_only_counts_when_a_sheet_document_is_created():
    import_body = _between(_src(), "async function libraryImportFiles(fileList)", "export function openLibrary")
    body = _between(import_body, "if (isSpreadsheet) {", "} else {")

    assert "let createdSheets = 0;" in body
    assert "createdSheets++;" in body
    assert "if (!createdSheets)" in body
    assert "Spreadsheet import failed: no readable sheets" in body
