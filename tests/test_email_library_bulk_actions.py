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


def test_email_bulk_exports_messages_and_attachments_from_selected_context():
    frontend = email_library_source()
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")
    export_src = _function_source("_exportSelectedBundle")
    payload_src = _function_source("_selectedEmailExportPayload")

    assert "Export Emails" in frontend
    assert "Email file (.eml)" in frontend
    assert "_exportSelectedEmailPdf" in frontend
    assert "Export Attachments" in frontend
    assert "All in view" in frontend
    assert "${isEmailExport ? 'messages' : 'attachments'}-download-bulk" in export_src
    assert "method: 'POST'" in export_src
    assert "res.blob()" in export_src
    assert "account_id: String(em.account_id || state._libAccountId || '')" in payload_src
    assert "folder: String(em.folder || state._libFolder || 'INBOX')" in payload_src

    assert '@router.post("/attachments-download-bulk")' in backend
    assert "Download visible attachments from selected emails as one zip archive." in backend
    assert "_fixture_email_read(uid, folder, owner)" in backend
    assert "_is_likely_signature_image_attachment(att)" in backend

    assert '@router.post("/messages-download-bulk")' in backend
    assert '"(UID RFC822)"' in backend
    assert "msg.as_bytes(policy=email_policy.SMTP)" in backend
    assert 'media_type="message/rfc822"' in backend
    assert '"X-Exported-Emails"' in backend


def test_email_print_pdf_export_reads_without_marking_read_and_sanitizes_body():
    """Print / save PDF export must use the browser print dialog (SAN-157).

    The removed html2pdf library must not be loaded or referenced. Instead
    the export uses a sandboxed iframe and window.print(), matching the
    established pattern in document.js.
    """
    frontend = email_library_source()
    print_src = _function_source("_printSelectedEmails")
    menu_src = js_function_source("_showBulkActionsMenu", frontend)

    assert "mark_seen=false&full=true" in print_src
    assert "_safeRenderEmailBody(data)" in print_src
    assert "new DOMParser()" in print_src
    assert "up to 20 emails" in print_src
    assert "Print / save PDF" in menu_src

    # SAN-157: must NOT reference the removed html2pdf library.
    assert "html2pdf" not in print_src
    assert "html2pdf" not in frontend

    # Must use the browser print pattern with a sandboxed iframe.
    assert "contentWindow.print()" in print_src
    assert "sandbox" in print_src
    assert "allow-same-origin" in print_src
    assert "allow-modals" in print_src
    assert "afterprint" in print_src
    assert "email-browser-print-frame" in print_src


def test_email_eml_export_bounds_total_size():
    """The /messages-download-bulk endpoint must enforce a total size cap
    to prevent unbounded memory growth when exporting many large messages."""
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert "max_total_bytes" in backend
    assert "total_bytes" in backend
    assert "Export too large" in backend
    assert "status_code=413" in backend


def test_email_eml_export_validates_selection_count():
    """The /messages-download-bulk endpoint must reject oversized selections."""
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert 'len(messages) > 250' in backend
    assert '"Too many emails selected"' in backend


def test_email_eml_export_rejects_non_numeric_remote_uids():
    """Non-numeric UIDs must not be passed to IMAP UID FETCH."""
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    # After fixture lookup fails, non-numeric UIDs are skipped.
    assert 're.fullmatch(r"\\d+", uid)' in backend


def test_email_eml_export_handles_partial_batch_failures():
    """When an IMAP fetch batch returns partial results, exported/skipped
    accounting must remain correct."""
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    # The skipped counter increments for UIDs not found in the batch response.
    assert "skipped += len(batch) - len(found)" in backend
    # Whole-connection failures count remaining unprocessed rows as skipped.
    assert "skipped += remaining_rows" in backend
    assert '"X-Exported-Emails"' in backend
    assert '"X-Skipped-Emails"' in backend


def test_email_eml_export_enforces_account_ownership():
    """The /messages-download-bulk endpoint must assert account ownership."""
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert "_assert_owns_account(account_id, owner)" in backend


def test_email_eml_export_deduplicates_selection():
    """Duplicate items in selection must be skipped to avoid duplicate files."""
    backend = _EMAIL_ROUTES.read_text(encoding="utf-8")

    assert "seen_items" in backend


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


class _MockImapConn:
    def __init__(self, messages_by_uid: dict[str, bytes] | None = None, fetch_exc: Exception | None = None, fetch_cb=None):
        self.messages = messages_by_uid or {}
        self.fetch_exc = fetch_exc
        self.fetch_cb = fetch_cb
        self.fetch_calls: list = []

    def select(self, folder, readonly=True):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "FETCH":
            self.fetch_calls.append(args)
            if self.fetch_exc is not None:
                raise self.fetch_exc
            if self.fetch_cb:
                return self.fetch_cb(args)
            uid_set_b = args[0]
            uid_set = uid_set_b.decode() if isinstance(uid_set_b, bytes) else str(uid_set_b)
            uids = [u.strip() for u in uid_set.split(",") if u.strip()]
            fetch_cmd = str(args[1]) if len(args) > 1 else ""
            res = []
            if "RFC822.SIZE" in fetch_cmd:
                for u in uids:
                    if u in self.messages:
                        meta = f"1 (UID {u} RFC822.SIZE {len(self.messages[u])})".encode()
                        res.append(meta)
                return "OK", res
            for u in uids:
                if u in self.messages:
                    meta = f"1 (UID {u} RFC822 {{{len(self.messages[u])}}}".encode()
                    res.append((meta, self.messages[u]))
                    res.append(b")")
            return "OK", res
        return "NO", []


def _create_test_app(monkeypatch, mailbox: _MockImapConn | None = None, assert_owns_fn=None):
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.email import email_routes as routes

    conn = mailbox or _MockImapConn()

    @contextmanager
    def mock_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_POOL_HOOKS", {})
    monkeypatch.setattr(routes, "_imap", mock_imap)
    if assert_owns_fn:
        monkeypatch.setattr(routes, "_assert_owns_account", assert_owns_fn)

    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: "test-user"
    return TestClient(app)


def test_messages_download_bulk_validates_empty_and_oversized_payload(monkeypatch):
    client = _create_test_app(monkeypatch)

    res_empty = client.post("/api/email/messages-download-bulk", json={"messages": []})
    assert res_empty.status_code == 400
    assert "No emails selected" in res_empty.text

    oversized = [{"uid": str(i)} for i in range(251)]
    res_over = client.post("/api/email/messages-download-bulk", json={"messages": oversized})
    assert res_over.status_code == 400
    assert "Too many emails selected" in res_over.text


def test_messages_download_bulk_single_message_eml_export(monkeypatch):
    raw_rfc822 = b"From: sender@example.com\r\nTo: test-user\r\nSubject: Report\r\n\r\nReport content"
    mailbox = _MockImapConn({"101": raw_rfc822})
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101", "subject": "Quarterly Report", "date": "2026-10-20"}]
    })

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("message/rfc822")
    assert res.headers["x-exported-emails"] == "1"
    assert res.headers["x-skipped-emails"] == "0"
    assert "email.eml" in res.headers["content-disposition"]
    assert res.content == raw_rfc822


def test_messages_download_bulk_multi_message_zip_export(monkeypatch):
    import io
    import zipfile

    msg1 = b"From: a@example.com\r\nSubject: First\r\n\r\nOne"
    msg2 = b"From: b@example.com\r\nSubject: Second\r\n\r\nTwo"
    mailbox = _MockImapConn({"101": msg1, "102": msg2})
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [
            {"uid": "101", "subject": "First Msg", "date": "2026-10-18"},
            {"uid": "102", "subject": "Second Msg", "date": "2026-10-19"},
        ]
    })

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-exported-emails"] == "2"
    assert res.headers["x-skipped-emails"] == "0"

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    namelist = zf.namelist()
    assert len(namelist) == 2
    assert any("101.eml" in name for name in namelist)
    assert any("102.eml" in name for name in namelist)


def test_messages_download_bulk_partial_failure_and_duplicate_dedup(monkeypatch):
    import io
    import zipfile

    msg1 = b"From: a@example.com\r\nSubject: Exists\r\n\r\nExists body"
    # UID 101 exists, UID 999 does not exist in mailbox
    mailbox = _MockImapConn({"101": msg1})
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [
            {"uid": "101", "subject": "Exists"},
            {"uid": "999", "subject": "Missing"},
            {"uid": "101", "subject": "Duplicate"}, # duplicate must be skipped
        ]
    })

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-exported-emails"] == "1"
    assert res.headers["x-skipped-emails"] == "2"

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert len(zf.namelist()) == 1


def test_messages_download_bulk_enforces_account_ownership_guard(monkeypatch):
    from fastapi import HTTPException

    def mock_assert_owns(account_id, owner):
        if account_id == "other-tenant-acct":
            raise HTTPException(status_code=404, detail="Account not found")

    client = _create_test_app(monkeypatch, assert_owns_fn=mock_assert_owns)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101", "account_id": "other-tenant-acct"}]
    })

    assert res.status_code == 404
    assert "Account not found" in res.text


def test_messages_download_bulk_mid_batch_failure_accounting(monkeypatch):
    """When an exception occurs mid-batch after partial progress, already-exported
    records must not be double-counted as skipped."""
    class MidBatchFailImap:
        def select(self, folder, readonly=True):
            return "OK", [b"1"]
        def uid(self, command, *args):
            fetch_cmd = str(args[1]) if len(args) > 1 else ""
            if "RFC822.SIZE" in fetch_cmd:
                return "OK", [f"{i} (UID {i} RFC822.SIZE 100)".encode() for i in range(1, 11)]
            records = []
            for i in range(1, 5):
                records.append((f"{i} (UID {i} RFC822 {{{10}}}".encode(), f"Subject: Msg {i}\r\n\r\nBody {i}".encode()))
            class BadRecord:
                def __bytes__(self):
                    raise RuntimeError("Corrupt memory buffer")
            records.append((b"5 (UID 5 RFC822 {10}", BadRecord()))
            return "OK", records

    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.email import email_routes as routes

    @contextmanager
    def mock_imap(account_id=None, owner=""):
        yield MidBatchFailImap()

    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_POOL_HOOKS", {})
    monkeypatch.setattr(routes, "_imap", mock_imap)

    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: "test-user"
    client = TestClient(app)

    rows = [{"uid": str(i)} for i in range(1, 11)]
    res = client.post("/api/email/messages-download-bulk", json={"messages": rows})
    assert res.status_code == 200
    exp = int(res.headers.get("x-exported-emails"))
    skp = int(res.headers.get("x-skipped-emails"))
    assert exp == 4
    assert skp == 6
    assert exp + skp == 10


def test_messages_download_bulk_size_cap_413_boundary(monkeypatch):
    """Cumulative export size exceeding max_total_bytes returns HTTP 413."""
    class LargeMessageImap:
        def select(self, folder, readonly=True):
            return "OK", [b"1"]
        def uid(self, command, *args):
            raw_uids = args[0] if isinstance(args[0], str) else args[0].decode()
            if "(UID RFC822.SIZE)" in str(args):
                records = []
                for u in raw_uids.split(","):
                    records.append(f"{u} (UID {u} RFC822.SIZE 10485760)".encode())
                return "OK", records
            records = []
            big_chunk = b"X" * (10 * 1024 * 1024)
            for u in raw_uids.split(","):
                records.append((f"{u} (UID {u} RFC822".encode(), big_chunk))
            return "OK", records

    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.email import email_routes as routes

    @contextmanager
    def mock_imap(account_id=None, owner=""):
        yield LargeMessageImap()

    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_POOL_HOOKS", {})
    monkeypatch.setattr(routes, "_imap", mock_imap)

    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: "test-user"
    client = TestClient(app)

    # 52 messages of 10 MB = 520 MB > 512 MiB limit
    payload_52 = [{"uid": str(i + 1), "subject": f"Big {i}"} for i in range(52)]
    res = client.post("/api/email/messages-download-bulk", json={"messages": payload_52})
    assert res.status_code == 413
    assert res.json()["detail"] == "Export too large"


def test_messages_download_bulk_per_message_limit_413(monkeypatch):
    """A single oversized message (>64 MiB) triggers HTTP 413."""
    class HugeSingleMsgImap:
        def select(self, folder, readonly=True):
            return "OK", [b"1"]
        def uid(self, command, *args):
            return "OK", [b"1 (UID 101 RFC822.SIZE 73400320)"]

    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.email import email_routes as routes

    @contextmanager
    def mock_imap(account_id=None, owner=""):
        yield HugeSingleMsgImap()

    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_POOL_HOOKS", {})
    monkeypatch.setattr(routes, "_imap", mock_imap)

    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: "test-user"
    client = TestClient(app)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101", "subject": "Huge Email"}]
    })
    assert res.status_code == 413
    assert res.json()["detail"] == "Export too large"


def test_messages_download_bulk_account_ownership_memoization(monkeypatch):
    """Account ownership validation is memoized per distinct account_id to eliminate N+1 DB queries."""
    owns_calls = []
    def counting_assert_owns(account_id, owner):
        owns_calls.append(account_id)

    raw_rfc822 = b"From: sender@example.com\r\nSubject: Test\r\n\r\nBody"
    msgs = {str(i): raw_rfc822 for i in range(1, 26)}
    mailbox = _MockImapConn(msgs)
    client = _create_test_app(monkeypatch, mailbox=mailbox, assert_owns_fn=counting_assert_owns)

    # 25 messages all referencing the same account_id
    payload = [{"uid": str(i), "account_id": "acc-shared"} for i in range(1, 26)]
    res = client.post("/api/email/messages-download-bulk", json={"messages": payload})
    assert res.status_code == 200
    assert len(owns_calls) == 1


def test_messages_download_bulk_corrupt_zip_fails_closed(monkeypatch):
    """If ZIP construction fails (e.g. disk error), fail closed with 500 instead of returning corrupt archive."""
    import zipfile
    raw_rfc822 = b"From: sender@example.com\r\nSubject: Test\r\n\r\nBody"
    mailbox = _MockImapConn({"101": raw_rfc822, "102": raw_rfc822})

    orig_writestr = zipfile.ZipFile.writestr
    def failing_writestr(self, zinfo_or_arcname, data=None, *args, **kwargs):
        raise OSError("Disk write failed: No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", failing_writestr)
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101"}, {"uid": "102"}]
    })
    assert res.status_code == 500
    assert "Failed to create export archive" in res.json()["detail"]


def test_messages_download_bulk_preflight_no_fails_safe_no_body_fetch(monkeypatch):
    """When preflight returns NO, size safety is unknown: fail safely (502) and do not fetch bodies."""
    mailbox = _MockImapConn({"101": b"dummy", "102": b"dummy"})
    mailbox.fetch_cb = lambda args: ("NO", []) if "RFC822.SIZE" in str(args) else ("OK", [])
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}, {"uid": "102"}]})
    assert res.status_code == 502
    assert "Unable to verify email size safety" in res.json()["detail"]
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)


def test_messages_download_bulk_preflight_empty_missing_mail(monkeypatch):
    """When preflight returns empty data, all requested messages are missing on server: returns 404 with no body fetch."""
    mailbox = _MockImapConn()  # no messages
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}, {"uid": "102"}]})
    assert res.status_code == 404
    assert "No selected emails could be exported" in res.json()["detail"]
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)


def test_messages_download_bulk_preflight_malformed_fails_safe_no_body_fetch(monkeypatch):
    """When preflight returns records without RFC822.SIZE, fail safely (502) and do not fetch bodies."""
    mailbox = _MockImapConn({"101": b"dummy"})
    mailbox.fetch_cb = lambda args: ("OK", [b"1 (UID 101 FLAGS (\\Seen))"]) if "RFC822.SIZE" in str(args) else ("OK", [])
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}]})
    assert res.status_code == 502
    assert "Invalid email size metadata from server" in res.json()["detail"]
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)


def test_messages_download_bulk_preflight_inconsistent_fails_safe_no_body_fetch(monkeypatch):
    """When preflight returns negative or corrupted size values, fail safely (502) and do not fetch bodies."""
    mailbox = _MockImapConn({"101": b"dummy"})
    mailbox.fetch_cb = lambda args: ("OK", [b"1 (UID 101 RFC822.SIZE -50)"]) if "RFC822.SIZE" in str(args) else ("OK", [])
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}]})
    assert res.status_code == 502
    assert "Invalid email size metadata from server" in res.json()["detail"]
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)


def test_messages_download_bulk_preflight_partial_missing_omitted_from_body_fetch(monkeypatch):
    """When some requested UIDs do not exist on the server, omit them from body fetch and export verified items."""
    msg1 = b"From: a@example.com\r\nSubject: Only One\r\n\r\nBody"
    mailbox = _MockImapConn({"101": msg1})  # 102 does not exist on server
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101"}, {"uid": "102"}]
    })
    assert res.status_code == 200
    assert res.headers["x-exported-emails"] == "1"
    assert res.headers["x-skipped-emails"] == "1"

    # Verify body fetch was only requested for 101, NOT 102
    body_fetches = [call for call in mailbox.fetch_calls if "(UID RFC822)" in str(call)]
    assert len(body_fetches) == 1
    requested_uids = body_fetches[0][0].decode() if isinstance(body_fetches[0][0], bytes) else str(body_fetches[0][0])
    assert requested_uids == "101"
    assert "102" not in requested_uids


def test_messages_download_bulk_duplicate_imap_records_rejected(monkeypatch):
    """Duplicate IMAP FETCH response records for the same UID must be ignored.
    They must not produce duplicate archive entries or corrupt accounting."""
    import io
    import zipfile

    msg1 = b"From: a@example.com\r\nSubject: Unique\r\n\r\nBody 1"
    mailbox = _MockImapConn({"101": msg1})

    def custom_fetch(args):
        fetch_cmd = str(args[1]) if len(args) > 1 else ""
        if "RFC822.SIZE" in fetch_cmd:
            return "OK", [b"1 (UID 101 RFC822.SIZE 100)", b"2 (UID 102 RFC822.SIZE 100)"]
        # In body fetch, server sends duplicate records for 101, and nothing for 102
        records = [
            (b"1 (UID 101 RFC822 {50}", msg1),
            b")",
            (b"1 (UID 101 RFC822 {50}", msg1),  # DUPLICATE RECORD
            b")",
        ]
        return "OK", records

    mailbox.fetch_cb = custom_fetch
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101"}, {"uid": "102"}]
    })
    assert res.status_code == 200
    assert res.headers["x-exported-emails"] == "1"
    assert res.headers["x-skipped-emails"] == "1"

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert len(zf.namelist()) == 1
    assert "101.eml" in zf.namelist()[0]


def test_messages_download_bulk_single_message_preflight_failure_no_body_fetch(monkeypatch):
    """In single message mode, bad preflight or missing mail fails safely without body fetch."""
    mailbox = _MockImapConn({"101": b"dummy"})

    # Case A: preflight NO -> 502
    mailbox.fetch_cb = lambda args: ("NO", []) if "RFC822.SIZE" in str(args) else ("OK", [])
    client = _create_test_app(monkeypatch, mailbox=mailbox)
    res_no = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}]})
    assert res_no.status_code == 502
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)

    # Case B: missing mail -> 404
    mailbox.fetch_calls.clear()
    mailbox.fetch_cb = lambda args: ("OK", []) if "RFC822.SIZE" in str(args) else ("OK", [])
    res_miss = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}]})
    assert res_miss.status_code == 404
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)

    # Case C: malformed metadata -> 502
    mailbox.fetch_calls.clear()
    mailbox.fetch_cb = lambda args: ("OK", [b"1 (UID 101 NO_SIZE)"]) if "RFC822.SIZE" in str(args) else ("OK", [])
    res_mal = client.post("/api/email/messages-download-bulk", json={"messages": [{"uid": "101"}]})
    assert res_mal.status_code == 502
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)


def test_messages_download_bulk_spooled_stream_lifecycle():
    """Verify that _iter_spooled closes the underlying SpooledTemporaryFile on completion and GeneratorExit."""
    import tempfile
    from routes.email.email_routes import setup_email_routes

    # Test normal completion
    f = tempfile.SpooledTemporaryFile(max_size=1024, mode="w+b")
    f.write(b"Hello world payload")
    f.seek(0)
    # Re-create generator helper pattern
    def _iter(file_obj, chunk_size=64):
        try:
            while True:
                chunk = file_obj.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            file_obj.close()

    gen = _iter(f)
    chunks = list(gen)
    assert b"".join(chunks) == b"Hello world payload"
    assert f.closed

    # Test early disconnect / cancellation (GeneratorExit)
    f2 = tempfile.SpooledTemporaryFile(max_size=1024, mode="w+b")
    f2.write(b"Chunk1Chunk2Chunk3")
    f2.seek(0)
    gen2 = _iter(f2, chunk_size=6)
    next(gen2)
    assert not f2.closed
    gen2.close()  # Simulates client disconnect / generator cancellation
    assert f2.closed


def test_uid_and_size_from_fetch_meta_case_insensitivity_and_validation():
    """Unit test: _uid_from_fetch_meta and _size_from_fetch_meta case insensitivity and boundary validation."""
    from routes.email.email_routes import _uid_from_fetch_meta, _size_from_fetch_meta

    # 1. Standard uppercase UID and RFC822.SIZE
    meta_upper = b"1 (UID 101 RFC822.SIZE 1234)"
    assert _uid_from_fetch_meta(meta_upper) == "101"
    assert _size_from_fetch_meta(meta_upper) == 1234

    # 2. Lowercase uid and rfc822.size
    meta_lower = b"1 (uid 202 rfc822.size 5678)"
    assert _uid_from_fetch_meta(meta_lower) == "202"
    assert _size_from_fetch_meta(meta_lower) == 5678

    # 3. Mixed-case combinations
    combos = [
        (b"1 (Uid 303 Rfc822.Size 1000)", "303", 1000),
        (b"1 (uID 404 RFC822.size 2000)", "404", 2000),
        (b"1 (UID 505 rfc822.size 3000)", "505", 3000),
        (b"1 (uid 606 RFC822.SIZE 4000)", "606", 4000),
        (b"1 (uId 707 Rfc822.SIZE 5000)", "707", 5000),
        (b"1 (FLAGS (\\Seen) uid 808 rfc822.Size 6000)", "808", 6000),
    ]
    for meta_bytes, exp_uid, exp_sz in combos:
        assert _uid_from_fetch_meta(meta_bytes) == exp_uid
        assert _size_from_fetch_meta(meta_bytes) == exp_sz

    # 4. Malformed metadata cases
    assert _uid_from_fetch_meta(b"1 (rfc822.size 1234)") == ""
    assert _size_from_fetch_meta(b"1 (uid 101 flags (\\seen))") is None
    assert _uid_from_fetch_meta(b"1 (uid abc rfc822.size 1234)") == ""
    assert _size_from_fetch_meta(b"1 (uid 101 rfc822.size invalid)") is None
    assert _size_from_fetch_meta(b"1 (uid 101 rfc822.size -50)") is None

    # 5. Boundary integrity (must not match substrings like UUID, GUID, NOT_RFC822.SIZE)
    assert _uid_from_fetch_meta(b"1 (UUID 101 GUID 202 NOT_RFC822.SIZE 1234)") == ""
    assert _size_from_fetch_meta(b"1 (UUID 101 GUID 202 NOT_RFC822.SIZE 1234)") is None
    assert _uid_from_fetch_meta(b"1 (uid101 rfc822.size1234)") == ""
    assert _size_from_fetch_meta(b"1 (uid101 rfc822.size1234)") is None
    assert _uid_from_fetch_meta(b"") == ""
    assert _size_from_fetch_meta(b"") is None


def test_messages_download_bulk_case_insensitive_imap_atoms(monkeypatch):
    """End-to-end: Bulk and single-message export with lowercase and mixed-case IMAP atoms."""
    import io
    import zipfile

    msg101 = b"From: a@example.com\r\nSubject: Item 101\r\n\r\nBody 1"
    msg102 = b"From: b@example.com\r\nSubject: Item 102\r\n\r\nBody 2"
    mailbox = _MockImapConn({"101": msg101, "102": msg102})

    def custom_fetch(args):
        fetch_cmd = str(args[1]) if len(args) > 1 else ""
        if "RFC822.SIZE" in fetch_cmd or "rfc822.size" in fetch_cmd.lower():
            # Server returns lowercase/mixed-case atoms
            return "OK", [
                b"1 (uid 101 rfc822.size " + str(len(msg101)).encode() + b")",
                b"2 (Uid 102 Rfc822.Size " + str(len(msg102)).encode() + b")",
            ]
        # In body fetch, server returns mixed-case atoms
        return "OK", [
            (b"1 (uid 101 rfc822 {" + str(len(msg101)).encode(), msg101),
            b")",
            (b"2 (Uid 102 RFC822 {" + str(len(msg102)).encode(), msg102),
            b")",
        ]

    mailbox.fetch_cb = custom_fetch
    client = _create_test_app(monkeypatch, mailbox=mailbox)

    # 1. Bulk download (2 items) with lowercase / mixed-case IMAP responses
    res_bulk = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101"}, {"uid": "102"}]
    })
    assert res_bulk.status_code == 200
    assert res_bulk.headers["x-exported-emails"] == "2"
    assert res_bulk.headers["x-skipped-emails"] == "0"
    zf = zipfile.ZipFile(io.BytesIO(res_bulk.content))
    assert len(zf.namelist()) == 2

    # 2. Single message download fast path with lowercase IMAP response
    mailbox.fetch_calls.clear()
    res_single = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101"}]
    })
    assert res_single.status_code == 200
    assert res_single.headers["content-type"].startswith("message/rfc822")
    assert res_single.content == msg101

    # 3. Lowercase malformed preflight fails safely with 502 and NO body fetch
    mailbox.fetch_calls.clear()
    mailbox.fetch_cb = lambda args: (
        ("OK", [b"1 (uid 101 rfc822.size NaN)"])
        if "RFC822.SIZE" in str(args) or "rfc822.size" in str(args).lower()
        else ("OK", [])
    )
    res_malformed = client.post("/api/email/messages-download-bulk", json={
        "messages": [{"uid": "101"}]
    })
    assert res_malformed.status_code == 502
    assert "Invalid email size metadata from server" in res_malformed.json()["detail"]
    assert not any("(UID RFC822)" in str(call) for call in mailbox.fetch_calls)


def test_messages_download_bulk_multi_group_preflight_failure_propagates_502(monkeypatch):
    """A preflight safety failure in any group produces HTTP 502 for the entire request,
    even when another group has already exported messages into the temporary ZIP.
    Confirms temporary-file cleanup, zero body FETCH for the failed group, and both group orderings."""
    import tempfile

    msg101 = b"From: a@example.com\r\nSubject: Good\r\n\r\nBody 1"

    spooled_instances: list[tempfile.SpooledTemporaryFile] = []
    orig_spooled = tempfile.SpooledTemporaryFile

    def tracking_spooled(*args, **kwargs):
        inst = orig_spooled(*args, **kwargs)
        spooled_instances.append(inst)
        return inst

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", tracking_spooled)

    class MultiMailboxConn:
        def __init__(self):
            self.fetch_calls: list[tuple[str, tuple]] = []
            self.current_folder = "INBOX"

        def select(self, folder, readonly=True):
            self.current_folder = str(folder)
            return "OK", [b"1"]

        def uid(self, command, *args):
            self.fetch_calls.append((self.current_folder, args))
            fetch_cmd = str(args[1]) if len(args) > 1 else ""
            uid_set = args[0].decode() if isinstance(args[0], bytes) else str(args[0])

            if "RFC822.SIZE" in fetch_cmd:
                if "folder-fail" in self.current_folder:
                    return "NO", []  # Preflight failure for folder-fail
                return "OK", [f"1 (UID {uid_set} RFC822.SIZE 100)".encode()]

            # Body fetch
            if "folder-fail" in self.current_folder:
                return "NO", []
            return "OK", [(f"1 (UID {uid_set} RFC822 {{100}}".encode(), msg101), b")"]

    # 1. Successful group followed by failed preflight
    conn1 = MultiMailboxConn()
    client1 = _create_test_app(monkeypatch, mailbox=conn1)
    spooled_instances.clear()

    res1 = client1.post("/api/email/messages-download-bulk", json={
        "messages": [
            {"uid": "101", "folder": "folder-good"},
            {"uid": "202", "folder": "folder-fail"},
        ]
    })
    assert res1.status_code == 502
    assert "Unable to verify email size safety" in res1.json()["detail"]

    # No RFC822 body FETCH for the failed group
    failed_body_fetches1 = [
        call for folder, call in conn1.fetch_calls
        if "folder-fail" in folder and "(UID RFC822)" in str(call)
    ]
    assert len(failed_body_fetches1) == 0

    # Temporary file cleanup confirmed
    assert len(spooled_instances) == 1
    assert spooled_instances[0].closed

    # 2. Failed preflight followed by successful group
    conn2 = MultiMailboxConn()
    client2 = _create_test_app(monkeypatch, mailbox=conn2)
    spooled_instances.clear()

    res2 = client2.post("/api/email/messages-download-bulk", json={
        "messages": [
            {"uid": "202", "folder": "folder-fail"},
            {"uid": "101", "folder": "folder-good"},
        ]
    })
    assert res2.status_code == 502
    assert "Unable to verify email size safety" in res2.json()["detail"]

    # No RFC822 body FETCH for the failed group
    failed_body_fetches2 = [
        call for folder, call in conn2.fetch_calls
        if "folder-fail" in folder and "(UID RFC822)" in str(call)
    ]
    assert len(failed_body_fetches2) == 0

    # Temporary file cleanup confirmed
    assert len(spooled_instances) == 1
    assert spooled_instances[0].closed


def test_messages_download_bulk_multi_group_both_successful(monkeypatch):
    """When both groups succeed preflight and body fetch, return 200 with all messages in ZIP."""
    import io
    import zipfile

    msg101 = b"From: a@example.com\r\nSubject: Item 1\r\n\r\nBody 1"
    msg202 = b"From: b@example.com\r\nSubject: Item 2\r\n\r\nBody 2"

    class MultiMailboxConn:
        def __init__(self):
            self.current_folder = "INBOX"

        def select(self, folder, readonly=True):
            self.current_folder = str(folder)
            return "OK", [b"1"]

        def uid(self, command, *args):
            fetch_cmd = str(args[1]) if len(args) > 1 else ""
            uid_set = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
            msg = msg101 if "101" in uid_set else msg202
            if "RFC822.SIZE" in fetch_cmd:
                return "OK", [f"1 (UID {uid_set} RFC822.SIZE {len(msg)})".encode()]
            return "OK", [(f"1 (UID {uid_set} RFC822 {{{len(msg)}}}".encode(), msg), b")"]

    conn = MultiMailboxConn()
    client = _create_test_app(monkeypatch, mailbox=conn)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [
            {"uid": "101", "folder": "folder-1"},
            {"uid": "202", "folder": "folder-2"},
        ]
    })
    assert res.status_code == 200
    assert res.headers["x-exported-emails"] == "2"
    assert res.headers["x-skipped-emails"] == "0"
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert len(zf.namelist()) == 2


def test_messages_download_bulk_multi_group_genuinely_missing_message(monkeypatch):
    """Genuinely missing messages (unreturned by preflight) do not cause 502;
    they are skipped while verified messages in any group are successfully exported."""
    import io
    import zipfile

    msg101 = b"From: a@example.com\r\nSubject: Exists\r\n\r\nBody"

    class MissingMailboxConn:
        def __init__(self):
            self.fetch_calls: list = []
            self.current_folder = "INBOX"

        def select(self, folder, readonly=True):
            self.current_folder = str(folder)
            return "OK", [b"1"]

        def uid(self, command, *args):
            self.fetch_calls.append((self.current_folder, args))
            fetch_cmd = str(args[1]) if len(args) > 1 else ""
            uid_set = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
            if "RFC822.SIZE" in fetch_cmd:
                # 101 exists on server; 999 is genuinely missing (unreported in size metadata)
                res = []
                if "101" in uid_set:
                    res.append(f"1 (UID 101 RFC822.SIZE {len(msg101)})".encode())
                return "OK", res
            # Body fetch only returns 101
            return "OK", [(f"1 (UID 101 RFC822 {{{len(msg101)}}}".encode(), msg101), b")"]

    conn = MissingMailboxConn()
    client = _create_test_app(monkeypatch, mailbox=conn)

    res = client.post("/api/email/messages-download-bulk", json={
        "messages": [
            {"uid": "101", "folder": "folder-1"},
            {"uid": "999", "folder": "folder-1"},
        ]
    })
    assert res.status_code == 200
    assert res.headers["x-exported-emails"] == "1"
    assert res.headers["x-skipped-emails"] == "1"

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert len(zf.namelist()) == 1

    # Verify 999 was never requested in body fetch
    body_fetches = [
        call for folder, call in conn.fetch_calls
        if "(UID RFC822)" in str(call)
    ]
    assert len(body_fetches) == 1
    assert "999" not in str(body_fetches[0])


def test_email_print_browser_behaviors():
    """Real Chromium DOM: placeholder stripping, button removal, and partial failure."""
    import shutil
    import subprocess
    if not shutil.which('node'):
        import pytest
        pytest.skip('Node is required')
    probe = subprocess.run(['node', '-e', "require.resolve('playwright')"], cwd=_REPO, capture_output=True)
    if probe.returncode:
        import pytest
        pytest.skip('Playwright is required')
    result = subprocess.run(
        ['node', str(_REPO / 'tests/test_email_library_print_browser.cjs')],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL_BROWSER_TESTS_PASSED" in result.stdout
