"""Regression guard for issue #2467 — cross-document overwrite via a stale AI-edit diff.

document.js keeps the AI-edit diff state (``_diffModeActive`` / ``_diffOldContent`` /
``_diffNewContent`` / ``_diffChunks``) as a module-global singleton bound to whatever
document was active when the diff opened. ``handleDocUpdate()`` switches the active
document (``activeDocId``) whenever an AI update targets a different doc. If a pending
diff is not discarded first, a later tab switch (``switchToDoc`` → ``exitDiffMode(true)``)
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

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = (ROOT / "static/js/document.js").read_text()

GUARD = "if (_diffModeActive) exitDiffMode(true);"


def _function_body(src: str, signature: str) -> str:
    """Return the full text of a JS function, brace-matched from its signature."""
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {signature!r}")


HANDLE_DOC_UPDATE = _function_body(DOC_JS, "export function handleDocUpdate(data)")
STREAM_DOC_OPEN = _function_body(DOC_JS, "export function streamDocOpen(title, language)")


def test_handle_doc_update_discards_pending_diff():
    # A new AI update on a different document must not leave a stale diff bound
    # to the old doc, or a later tab switch / Accept-All overwrites the wrong doc.
    assert GUARD in HANDLE_DOC_UPDATE


def test_diff_discard_runs_before_active_doc_is_switched():
    # The discard must run while activeDocId still points at the previously
    # active doc, so exitDiffMode(true) restores and saves THAT doc — not the new
    # one. Any activeDocId reassignment inside handleDocUpdate must come after it.
    guard_at = HANDLE_DOC_UPDATE.index(GUARD)
    reassign_at = HANDLE_DOC_UPDATE.index("activeDocId = docId;")
    assert guard_at < reassign_at


def test_stream_doc_open_discards_pending_diff_before_switching():
    # The AI-creates-a-new-document path switches activeDocId inside
    # streamDocOpen (before any doc_update reaches handleDocUpdate), so the guard
    # must be here too — and before streamDocOpen reassigns activeDocId, or the
    # streamed new doc gets overwritten by the stale diff (the issue's own repro).
    assert GUARD in STREAM_DOC_OPEN
    assert STREAM_DOC_OPEN.index(GUARD) < STREAM_DOC_OPEN.index("activeDocId = docId;")


def test_diff_discard_reuses_the_existing_idiom():
    # Sanity: this exact guard is the established pattern (switchToDoc,
    # enterDiffMode, handleDocUpdate, streamDocOpen, …) — the fix reuses it
    # rather than inventing a new mechanism.
    assert DOC_JS.count(GUARD) >= 5
