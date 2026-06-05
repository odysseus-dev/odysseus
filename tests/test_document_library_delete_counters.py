"""Regression for #1809: document library counters must update after delete.

documentLibrary.js is a browser module with several DOM-only imports, so this
guards the relevant wiring at the source level. A single-card delete used to
remove the card and decrement `_libraryTotal`, but the header/chips render from
`_libraryLanguages`, which stayed stale until a full library refetch.
"""

from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "static/js/documentLibrary.js"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def test_single_delete_updates_language_counters_and_chips():
    text = _src()

    helper = _between(
        text,
        "function libraryRemoveDocumentFromState(docId)",
        "function libraryRenderGrid()",
    )
    assert "_libraryLanguages[lang]" in helper
    assert "delete _libraryLanguages[lang]" in helper
    assert "libraryRenderStats();" in helper
    assert "libraryRenderLangChips();" in helper

    delete_body = _between(
        text,
        "async function libraryDeleteSingle(docId, card)",
        "async function libraryBulkDelete()",
    )
    assert "libraryRemoveDocumentFromState(docId);" in delete_body
