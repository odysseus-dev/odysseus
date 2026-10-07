from pathlib import Path
from tests.helpers.document_source import document_source
from tests.helpers.js_modules import email_library_source, js_function_source


_REPO = Path(__file__).resolve().parents[1]
_EMAIL_ROUTES = _REPO / "routes" / "email" / "email_routes.py"
_EMAIL_MCP_SERVER = _REPO / "mcp_servers" / "email_server.py"
_EMAIL_FIXTURE_HELPER = _REPO / "scripts" / "ody_eval_email_fixture.py"


def _bulk_action_source() -> str:
    return js_function_source("_bulkAction")


def _function_source(name: str) -> str:
    return js_function_source(name)


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


def test_email_bulk_export_attachments_is_ui_only_selected_context():
    frontend = email_library_source()
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")
    export_src = frontend[
        frontend.index("async function _exportSelectedAttachments()"):
        frontend.index("\n}\n\n// URL-suffix helper", frontend.index("async function _exportSelectedAttachments()")) + 3
    ]
    payload_src = _function_source("_selectedEmailExportPayload")

    assert "Export Attachments" in frontend
    assert "All in view" in frontend
    assert "/api/email/attachments-download-bulk" in export_src
    assert "method: 'POST'" in export_src
    assert "res.blob()" in export_src
    assert "account_id: String(em.account_id || state._libAccountId || '')" in payload_src
    assert "folder: String(em.folder || state._libFolder || 'INBOX')" in payload_src

    assert '@router.post("/attachments-download-bulk")' in backend
    assert "Download visible attachments from selected emails as one zip archive." in backend
    assert "_fixture_email_read(uid, folder, owner)" in backend
    assert "_is_likely_signature_image_attachment(att)" in backend


def test_fixture_tag_filter_supports_receipt_tag_flow():
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert 'row.get("tags") or row.get("category_tags") or []' in backend
    assert 'elif str(filter_).startswith("tag:"):' in backend
    assert 'rows = [e for e in rows if tag_name in (e.get("tags") or [])]' in backend


def test_email_context_changes_clear_bulk_selection_state():
    """IMAP UIDs are folder/account scoped, so stale bulk selections must die.

    Folder, account, filter, quick-filter, attachment, and search basis changes
    must exit select mode before the next list/search view can run bulk actions.
    """
    text = email_library_source()
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

    assert "state._libFolder = _resolveEmailFolderAlias(e.target.value);" in text
    assert "state._libFilter = e.target.value;" in text
    assert "state._libHasAttachments = !state._libHasAttachments;" in text
    assert "const nextAccountId = btn.dataset.accId || null;" in text
    assert "state._libAccountId = nextAccountId;" in text
    assert text.count("_loadEmailsFresh();") >= 5
    assert "state._libSearchDraft = input.value;" in text


def test_email_refresh_uses_explicit_server_refresh_contract():
    """Manual refresh must bypass more than the browser/list response cache.

    The server has a durable index and pooled IMAP handles for speed. The
    refresh button should keep the old rows visible while asking the server to
    evict those fast paths and refetch the visible mailbox slice.
    """
    frontend = email_library_source()
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert "refresh=1&_=${Date.now()}" in frontend
    assert "refresh: int = Query(0)" in backend
    assert "manual_refresh = bool(refresh)" in backend
    assert "_invalidate_list_cache(account_id, folder)" in backend
    assert "_invalidate_imap_pool(account_id, owner)" in backend
    assert "cached_rows = {} if refresh else _email_index_rows" in backend


def test_fixture_email_requires_explicit_eval_flag():
    """A leftover fixture file must not replace a user's real mailbox.

    The deterministic fixture mailbox is useful for evals, but production
    routes and MCP tools must not activate it merely because
    data/fixture_email_messages.json exists.
    """
    routes = _EMAIL_ROUTES.read_text(encoding="utf-8")
    mcp = _EMAIL_MCP_SERVER.read_text(encoding="utf-8")
    helper = _EMAIL_FIXTURE_HELPER.read_text(encoding="utf-8")

    guard = 'os.environ.get("ODYSSEUS_EMAIL_FIXTURE") == "1" and _fixture_email_file().exists()'
    assert guard in routes
    assert guard in mcp
    assert 'old_fixture_env = os.environ.get("ODYSSEUS_EMAIL_FIXTURE")' in helper
    assert 'os.environ["ODYSSEUS_EMAIL_FIXTURE"] = "1"' in helper
    assert 'os.environ.pop("ODYSSEUS_EMAIL_FIXTURE", None)' in helper


def test_email_client_cache_drops_fixture_rows():
    """Old fixture rows in browser storage must not keep rendering."""
    frontend = email_library_source()

    assert "function _looksLikeFixtureEmailRow(row)" in frontend
    assert "function _libCacheHasFixtureRows(value)" in frontend
    assert "function _libDropCacheKey(key)" in frontend
    assert "older inbox message" in frontend
    assert "fixture-email-" in frontend
    assert "if (_libCacheHasFixtureRows(memory))" in frontend
    assert "if (_libCacheHasFixtureRows(stored))" in frontend
    assert "if (_libCacheHasFixtureRows(value))" in frontend


def test_email_compose_can_attach_gallery_images():
    """Compose attachments should support local files, documents, and Gallery images."""
    frontend_text = document_source()
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert "Upload from computer" in frontend_text
    assert 'data-ody-attach-kind="document"' in frontend_text
    assert "Gallery images" in frontend_text
    assert 'data-ody-attach-kind="gallery"' in frontend_text
    assert "api/gallery/library" in frontend_text
    assert "compose-from-odysseus" in frontend_text

    assert 'kind not in {"document", "gallery"}' in backend
    assert 'if kind == "gallery":' in backend
    assert "GalleryImage" in backend
    assert "_gallery_image_path(img.filename)" in backend
    assert "_stage_compose_file" in backend
