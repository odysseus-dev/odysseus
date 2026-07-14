from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_EMAIL_LIBRARY = _REPO / "static" / "js" / "emailLibrary.js"


def _bulk_action_source() -> str:
    text = _EMAIL_LIBRARY.read_text(encoding="utf-8")
    start = text.index("async function _bulkAction(action)")
    end = text.index("\n}\n\n// _extractName", start) + 3
    return text[start:end]


def _function_source(name: str) -> str:
    text = _EMAIL_LIBRARY.read_text(encoding="utf-8")
    start = text.index(f"function {name}")
    next_function = text.find("\nfunction ", start + 1)
    next_async = text.find("\nasync function ", start + 1)
    candidates = [idx for idx in (next_function, next_async) if idx != -1]
    end = min(candidates) if candidates else len(text)
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


def test_email_context_changes_clear_bulk_selection_state():
    """IMAP UIDs are folder/account scoped, so stale bulk selections must die.

    Folder, account, filter, quick-filter, attachment, and search basis changes
    must exit select mode before the next list/search view can run bulk actions.
    """
    text = _EMAIL_LIBRARY.read_text(encoding="utf-8")
    reset_src = _function_source("_resetBulkSelectionForContextChange")
    fresh_src = _function_source("_resetEmailListForFreshLoad")
    add_pill_src = _function_source("_addSearchPill")
    remove_pill_src = _function_source("_removeSearchPillAt")
    search_src = text[text.index("async function _doSearch()"):text.index("// Custom dropdown", text.index("async function _doSearch()"))]

    assert "state._selectedUids.clear()" in reset_src
    assert "state._selectMode = false" in reset_src
    assert "_updateBulkBar()" in reset_src

    assert "_resetBulkSelectionForContextChange()" in fresh_src
    assert "_resetBulkSelectionForContextChange({ rerender: true })" in add_pill_src
    assert "_resetBulkSelectionForContextChange({ rerender: true })" in remove_pill_src
    assert "_resetBulkSelectionForContextChange({ rerender: true })" in search_src

    assert "state._libFolder = e.target.value;" in text
    assert "state._libFilter = e.target.value;" in text
    assert "state._libHasAttachments = !state._libHasAttachments;" in text
    assert "state._libAccountId = btn.dataset.accId || null;" in text
    assert text.count("_loadEmailsFresh();") >= 5
    assert "state._libSearchDraft = input.value;" in text
