from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_EMAIL_LIBRARY = _REPO / "static" / "js" / "emailLibrary.js"


def _bulk_action_source() -> str:
    text = _EMAIL_LIBRARY.read_text(encoding="utf-8")
    start = text.index("async function _bulkAction(action)")
    end = text.index("\n}\n\n// _extractName", start) + 3
    return text[start:end]


def test_email_bulk_read_unread_calls_provider_write_routes():
    """Bulk read/unread must persist to IMAP/provider, not only mutate UI state.

    Regression for issue #800's email follow-up: list select -> Actions ->
    Mark Read used to update `em.is_read` locally and cache that fake state,
    then refresh from the provider made the message unread again.
    """
    src = _bulk_action_source()

    assert "Local toggle for now" not in src
    assert "mark-read" in src
    assert "mark-unread" in src
    assert "method: 'POST'" in src
    assert "_syncEmailReadState(uid, action === 'read')" in src


def test_email_bulk_read_unread_checks_backend_success_before_syncing_cache():
    src = _bulk_action_source()

    assert "data?.success === false" in src
    assert "throw new Error(data?.error" in src
    assert "_libCacheWriteBack()" in src
