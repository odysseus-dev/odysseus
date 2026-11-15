"""Regression guard for issue #2467 — cross-document overwrite via a stale AI-edit diff.

The AI-edit diff state (``_diffModeActive`` / ``_diffOldContent`` /
``_diffNewContent`` / ``_diffChunks``) is bound to the document that was active
when the diff opened. ``handleDocUpdate()`` switches the active document
(``activeDocId``) whenever an AI update targets another document. If a pending
diff is not discarded first, a later tab switch (``switchToDoc`` to ``exitDiffMode(true)``)
or Accept/Reject-All flushes the stale diff's content into the now-active document and
silently overwrites it.

The fix discards any pending diff while ``activeDocId`` still points at the
previously-active doc, mirroring the guard ``switchToDoc()`` and ``enterDiffMode()``
already use. It must run in BOTH places that switch the active document for an AI
update: ``handleDocUpdate()`` and ``streamDocOpen()``. The streamed path matters most —
when the AI creates a NEW document (the issue's own repro), ``streamDocOpen`` reassigns
``activeDocId`` first, so a guard only in ``handleDocUpdate`` would fire too late and
still overwrite the new doc. Kept as a static source check because document.js is
browser-coupled and not importable in pytest.
"""

from tests.helpers.document_source import document_source, function_body

DOC_JS = document_source()

GUARD = "if (_diffModeActive) exitDiffMode(true);"


HANDLE_DOC_UPDATE = function_body("handleDocUpdate")
STREAM_DOC_OPEN = function_body("streamDocOpen")


def _find_reassign(fn_body: str) -> int:
    for target in ("setActiveDocId(docId);", "activeDocId = docId;"):
        if target in fn_body:
            return fn_body.index(target)
    raise ValueError("activeDocId reassignment not found")


def test_handle_doc_update_discards_pending_diff():
    # A new AI update on a different document must not leave a stale diff bound
    # to the old doc, or a later tab switch / Accept-All overwrites the wrong doc.
    assert "if (_diffModeActive) exitDiffMode(true, { persist: data.doc_id !== activeDocId });" in HANDLE_DOC_UPDATE


def test_diff_discard_runs_before_active_doc_is_switched():
    # The discard must run while activeDocId still points at the previously
    # active doc, so exitDiffMode(true) restores and saves THAT doc - not the new
    # one. Any activeDocId reassignment inside handleDocUpdate must come after it.
    guard_at = HANDLE_DOC_UPDATE.index("if (_diffModeActive) exitDiffMode(true,")
    reassign_at = _find_reassign(HANDLE_DOC_UPDATE)
    assert guard_at < reassign_at


def test_stream_doc_open_discards_pending_diff_before_switching():
    # The AI-creates-a-new-document path switches activeDocId inside
    # streamDocOpen (before any doc_update reaches handleDocUpdate), so the guard
    # must be here too - and before streamDocOpen reassigns activeDocId, or the
    # streamed new doc gets overwritten by the stale diff (the issue's own repro).
    assert GUARD in STREAM_DOC_OPEN
    assert STREAM_DOC_OPEN.index(GUARD) < _find_reassign(STREAM_DOC_OPEN)


def test_diff_discard_reuses_the_existing_idiom():
    # Sanity: this exact guard is the established pattern (switchToDoc,
    # enterDiffMode, handleDocUpdate, streamDocOpen, …) — the fix reuses it
    # rather than inventing a new mechanism.
    assert DOC_JS.count(GUARD) >= 4


def test_same_document_update_does_not_save_stale_diff_over_new_server_content():
    assert "persist: data.doc_id !== activeDocId" in HANDLE_DOC_UPDATE
    assert "if (persist) saveDocument({ silent: true });" in DOC_JS
