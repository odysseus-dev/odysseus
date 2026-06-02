"""Issue #1160 — a closed document must not stay 'active' and leak into new chats.

Closing a document tab detaches it (session_id -> NULL) or deletes it, but the
in-memory active-document pointer was never cleared, so the last-resort doc
injection re-surfaced the closed doc in later, unrelated chats. The document
routes now call clear_active_document() on detach/delete; this pins that helper.
"""

from src.tool_implementations import (
    set_active_document,
    get_active_document,
    clear_active_document,
)


def test_clear_matching_id_resets_pointer():
    set_active_document("doc-123")
    assert get_active_document() == "doc-123"
    assert clear_active_document("doc-123") is True
    assert get_active_document() is None


def test_clear_non_matching_id_leaves_other_active_doc():
    set_active_document("doc-abc")
    # Closing a DIFFERENT document must not clobber the currently active one.
    assert clear_active_document("doc-xyz") is False
    assert get_active_document() == "doc-abc"


def test_clear_without_id_clears_unconditionally():
    set_active_document("doc-abc")
    assert clear_active_document() is True
    assert get_active_document() is None


def test_clear_when_already_none_is_safe():
    set_active_document(None)
    assert clear_active_document("doc-123") is False
    assert get_active_document() is None
