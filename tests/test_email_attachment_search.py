import sqlite3
from email.message import EmailMessage
from tests.helpers.document_source import document_source
from tests.helpers.js_modules import email_library_source


def test_attachment_filename_is_part_of_ui_index_search(tmp_path, monkeypatch):
    from routes import email_routes

    db_path = tmp_path / "mail.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE email_message_index (
        owner TEXT, account_key TEXT, folder TEXT, uid TEXT, message_id TEXT,
        subject TEXT, from_name TEXT, from_address TEXT, to_text TEXT, cc_text TEXT,
        date_iso TEXT, date_display TEXT, date_epoch REAL, size INTEGER, flags TEXT,
        has_attachments INTEGER, attachment_names TEXT, updated_at TEXT)"""
    )
    conn.execute(
        "INSERT INTO email_message_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("felix", "acct", "INBOX", "7", "<7@example>", "Quarterly files", "A", "a@example.com",
         "felix@example.com", "", "", "", 1, 10, "", 1, "PDPUK-tax-return.pdf", "now"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)

    rows, total, _ = email_routes._email_index_search(
        "felix", "acct", "INBOX", "PDPUK-tax", 20, global_search=False
    )

    assert total == 1
    assert rows[0]["uid"] == "7"


def test_signature_only_message_has_no_visible_attachments():
    from routes.email_helpers import _has_visible_attachments

    msg = EmailMessage()
    msg.set_content("Regards")
    msg.add_related(
        b"tiny-logo",
        maintype="image",
        subtype="png",
        cid="logo",
        filename="signature.png",
        disposition="inline",
    )

    assert _has_visible_attachments(msg) is False


def test_header_only_related_message_is_not_marked_as_attached():
    from routes.email_routes import _parse_email_list_record

    raw = (
        b"Content-Type: multipart/related; boundary=abc\r\n"
        b"Subject: Signature only\r\nFrom: a@example.com\r\n\r\n"
    )
    parsed = _parse_email_list_record(b"1 (UID 9 FLAGS () RFC822.SIZE 123)", raw)
    assert parsed["has_attachments"] is False


def test_real_attachment_remains_visible_alongside_signature():
    from routes.email_helpers import _has_visible_attachments

    msg = EmailMessage()
    msg.set_content("See attached")
    msg.add_attachment(b"report", maintype="application", subtype="pdf", filename="report.pdf")
    msg.add_attachment(b"tiny-logo", maintype="image", subtype="png", filename="logo.png")

    assert _has_visible_attachments(msg) is True


def test_remote_search_explicitly_checks_mime_filename_headers():
    from routes.email_routes import _email_imap_search_criteria

    criteria = _email_imap_search_criteria("report.pdf")
    assert "HEADER Content-Disposition" in criteria
    assert "HEADER Content-Type" in criteria


def test_forwarding_filters_signature_assets_and_mobile_export_stops_bubbling():
    inbox = open("static/js/emailInbox.js", encoding="utf-8").read()
    document = document_source()

    assert "const forwardedAttachments = mode === 'forward'" in inbox
    assert "forwardedAttachments.map" in inbox
    assert ": [];" in inbox[inbox.index("const forwardedAttachments"):inbox.index("content += '\\n---\\n'", inbox.index("const forwardedAttachments"))]
    assert "showExportMenu(e, e.currentTarget.getBoundingClientRect())" in document


def test_attachment_open_spins_icon_only():
    library = email_library_source()
    start = library.index("reader.querySelectorAll('.email-attachment-open')")
    end = library.index("reader.querySelectorAll('.email-attachment-download')", start)
    handler = library[start:end]

    assert "openBtn.appendChild(wp.element)" in handler
    assert "email-attachment-open-label" not in handler


def test_move_document_creates_destination_before_adopting_it():
    document = document_source()
    start = document.index("async function moveActiveDocumentToNewChat()")
    end = document.index("\n  function showDocTabMenu", start)
    handler = document[start:end]

    assert "_autoCreateSession({ adopt: false, forceNew: true })" in handler
    assert "await _moveDocToSession(activeDocId, sessionId, { switchChat: true })" in handler


def test_deferred_attachment_check_shows_feedback_and_repairs_stale_card_icon():
    library = email_library_source()
    start = library.index("function _loadDeferredAttachmentsIntoReader")
    end = library.index('\n// "Open in new tab"', start)
    loader = library[start:end]

    assert "Loading attachments…" in loader
    assert "matchingEmail.has_attachments = visibleCurrent.length > 0" in loader
    assert "querySelector('.email-card-attachment')?.remove()" in loader


def test_attachment_cache_backfill_preserves_message_id(tmp_path, monkeypatch):
    from routes import email_routes

    db_path = tmp_path / "mail.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE email_attachment_metadata_cache (
        owner TEXT, account_key TEXT, folder TEXT, uid TEXT, message_id TEXT,
        attachments_json TEXT, updated_at TEXT,
        PRIMARY KEY (owner, account_key, folder, uid))"""
    )
    conn.execute(
        """CREATE TABLE email_message_index (
        owner TEXT, account_key TEXT, folder TEXT, uid TEXT,
        has_attachments INTEGER, attachment_names TEXT, updated_at TEXT,
        PRIMARY KEY (owner, account_key, folder, uid))"""
    )
    conn.execute(
        "INSERT INTO email_message_index VALUES (?,?,?,?,?,?,?)",
        ("felix", "acct", "INBOX", "7", 1, "old.png", "before"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)

    signature = [{"filename": "signature.png", "size": 1000}]
    email_routes._email_attachment_meta_cache_put(
        "felix", "acct", "INBOX", "7", "<message@example>", signature
    )
    email_routes._email_attachment_meta_cache_put(
        "felix", "acct", "INBOX", "7", "", signature
    )

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT message_id FROM email_attachment_metadata_cache").fetchone()[0] == "<message@example>"
    assert conn.execute("SELECT has_attachments FROM email_message_index").fetchone()[0] == 0
    conn.close()


def test_single_email_tag_has_no_more_control():
    library = email_library_source()
    group = library[library.index("function _emailTagGroupHtml("):library.index("function _fitEmailCardTags(")]
    assert "if (visible.length === 1) return visible[0];" in group
    assert "if (visible.length === 2) return visible.join('');" in group
    assert group.index("if (visible.length === 2)") < group.index("email-tags-more-single")


def test_email_folder_and_filter_pickers_treat_their_buttons_as_inside_clicks():
    library = email_library_source()

    assert library.count(
        "bindMenuDismiss(menu, finishClose, e => !picker.contains(e.target))"
    ) == 2
    assert "bindMenuDismiss(menu, finishClose, e => picker.contains(e.target))" not in library


def test_empty_reply_has_two_editable_rows_and_reply_survives_compact_toolbar():
    inbox = open("static/js/emailInbox.js", encoding="utf-8").read()
    library = email_library_source()

    assert "<p><br></p><p><br></p>\\n" in inbox
    fit_start = library.index("function _fitReaderActions")
    fit_end = library.index("const _readerActionFitObserver", fit_start)
    fit = library[fit_start:fit_end]
    assert '[data-act="reply-all"], [data-act="forward"]' in fit
    assert '[data-act="reply"]' not in fit


def test_email_toolbar_places_attachment_before_link():
    document = document_source()
    toolbar_start = document.index('<div class="md-toolbar-items"')
    toolbar_end = document.index('</div>', toolbar_start)
    toolbar = document[toolbar_start:toolbar_end]

    assert toolbar.index('id="md-toolbar-attach-btn"') < toolbar.index('data-md="link"')
