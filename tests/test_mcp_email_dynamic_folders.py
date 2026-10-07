"""MCP email operations must use mailbox names returned by the IMAP server."""

import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


class MailboxIMAP:
    def __init__(self, folders, list_status="OK", select_status="OK", messages=True):
        self.folders = folders
        self.list_status = list_status
        self.select_status = select_status
        self.messages = messages
        self.calls = []

    def list(self):
        self.calls.append(("list",))
        return self.list_status, self.folders

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return self.select_status, [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"17" if self.messages else b""]
        if command == "FETCH":
            if args[-1] == "(UID)":
                return "OK", [b"1 (UID 17)"]
            return "OK", [(b"1 (UID 17)", (
                b"Subject: Folder discovery\r\n"
                b"From: Sender <sender@example.com>\r\n"
                b"Date: Mon, 05 Oct 2026 12:00:00 +0000\r\n"
                b"Message-ID: <folder-discovery@example.com>\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                b"Read this message from its actual sent mailbox.\r\n"
            ))]
        return "OK", []

    def logout(self):
        self.calls.append(("logout",))


def connect(monkeypatch, folders, **kwargs):
    conn = MailboxIMAP(folders, **kwargs)
    monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})
    monkeypatch.setattr(es, "_fixture_list_emails", lambda *args, **kwargs: None)
    monkeypatch.setattr(es, "_fixture_search_emails", lambda *args, **kwargs: None)
    monkeypatch.setattr(es, "_fixture_read_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(es, "_load_config", lambda account=None: {"account_name": "Test mailbox"})
    return conn


@pytest.mark.parametrize("line, expected", [
    (b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"', "[Gmail]/Sent Mail"),
    (b'(\\Sent) "/" "[Google Mail]/Sent Mail"', "[Google Mail]/Sent Mail"),
    (b'(\\Sent) "/" "[Gmail]/Gesendet"', "[Gmail]/Gesendet"),
    (b'(\\Sent) "/" "Sent \\"Copies\\""', 'Sent "Copies"'),
])
def test_list_sent_resolves_special_use_name(monkeypatch, line, expected):
    conn = connect(monkeypatch, [b'(\\HasNoChildren) "/" "INBOX"', line])

    results = es._list_emails(folder="Sent")

    assert len(results) == 1
    assert results[0]["_folder"] == expected
    assert ("select", es._q(expected), True) in conn.calls
    assert ("select", '"Sent"', True) not in conn.calls
    assert conn.calls.count(("list",)) == 1
    assert conn.calls[-1] == ("logout",)


def test_search_default_discovers_exact_selectable_role_mailboxes(monkeypatch):
    conn = connect(monkeypatch, [
        b'(\\Noselect) "/" "[Gmail]"',
        b'(\\Noselect \\Sent) "/" "Sent container"',
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\Sent) "/" "[Gmail]/Envoyes"',
        b'(\\All) "/" "[Gmail]/All Mail"',
        b'(\\Drafts) "/" "[Gmail]/Drafts"',
        b'(\\Junk) "/" "[Gmail]/Spam"',
        b'(\\Trash) "/" "[Gmail]/Trash"',
        b'(\\HasNoChildren) "/" "Project Notes"',
    ])

    results = es._search_emails("folder discovery")

    assert [call[1] for call in conn.calls if call[0] == "select"] == [
        '"INBOX"', '"[Gmail]/Envoyes"', '"[Gmail]/All Mail"',
    ]
    assert [item["_folder"] for item in results] == [
        "INBOX", "[Gmail]/Envoyes", "[Gmail]/All Mail",
    ]
    assert conn.calls.count(("list",)) == 1
    assert conn.calls[-1] == ("logout",)


def test_search_does_not_fabricate_missing_sent_or_archive(monkeypatch):
    conn = connect(monkeypatch, [b'(\\HasNoChildren) "/" "INBOX"'])

    results = es._search_emails("folder discovery")

    assert [item["_folder"] for item in results] == ["INBOX"]
    assert [call[1] for call in conn.calls if call[0] == "select"] == ['"INBOX"']


def test_search_explicit_folders_resolve_once_and_preserve_custom_labels(monkeypatch):
    conn = connect(monkeypatch, [
        b'(\\Sent) "/" "[Gmail]/Sent Mail"',
        b'(\\HasNoChildren) "/" "Sent workshop notes"',
    ])

    results = es._search_emails("folder discovery", folders=[
        "Sent", "[Gmail]/Sent Mail", "Sent workshop notes", "Sent workshop notes",
    ])

    assert [item["_folder"] for item in results] == [
        "[Gmail]/Sent Mail", "Sent workshop notes",
    ]
    assert conn.calls.count(("list",)) == 1


def test_search_failed_list_reports_failure_and_closes_connection(monkeypatch):
    conn = connect(monkeypatch, [b"LIST failed"], list_status="NO")

    with pytest.raises(RuntimeError):
        es._search_emails("folder discovery")

    assert not any(call[0] == "select" for call in conn.calls)
    assert conn.calls[-1] == ("logout",)


def test_search_explicit_folder_remains_usable_when_list_is_denied(monkeypatch):
    conn = connect(monkeypatch, [b"LIST denied"], list_status="NO")
    monkeypatch.setattr(es, "_indexed_search_emails", lambda *args, **kwargs: None)

    results = es._search_emails("folder discovery", folders=["Known shared folder"])

    assert len(results) == 1
    assert results[0]["_folder"] == "Known shared folder"
    assert ("select", '"Known shared folder"', True) in conn.calls
    assert any(call[:2] == ("uid", "SEARCH") for call in conn.calls)
    assert any(call[:2] == ("uid", "FETCH") for call in conn.calls)
    assert conn.calls.count(("list",)) == 1
    assert conn.calls.count(("logout",)) == 1
    assert conn.calls[-1] == ("logout",)


def test_sent_detection_uses_same_discovery_as_listing(monkeypatch):
    conn = connect(monkeypatch, [b'(\\Sent) "/" "[Gmail]/Gesendet"'])

    assert es._detect_sent_folder(conn) == "[Gmail]/Gesendet"


def test_move_does_not_treat_custom_name_substrings_as_roles(monkeypatch):
    conn = connect(monkeypatch, [b'(\\Junk) "/" "[Gmail]/Spam"'])

    assert es._move_message("17", "INBOX", "Conference spam speakers") is True

    assert ("uid", "MOVE", b"17", '"Conference spam speakers"') in conn.calls
    assert ("uid", "MOVE", b"17", '"[Gmail]/Spam"') not in conn.calls


def test_list_then_read_keeps_localized_folder_and_uid(monkeypatch):
    conn = connect(monkeypatch, [b'(\\Sent) "/" "[Gmail]/Gesendet"'])
    listed = es._list_emails(folder="Sent")[0]

    read = es._read_email(uid=listed["uid"], folder=listed["_folder"])

    assert read["uid"] == listed["uid"] == "17"
    assert read["_folder"] == listed["_folder"] == "[Gmail]/Gesendet"
    assert "actual sent mailbox" in read["body"]
    assert ("uid", "FETCH", b"17", "(BODY.PEEK[])") in conn.calls
    assert not any(call[0] == "select" and call[1] == '"Sent"' for call in conn.calls)


def test_read_generic_sent_resolves_localized_special_use_mailbox(monkeypatch):
    conn = connect(monkeypatch, [b'(\\Sent) "/" "[Gmail]/Gesendet"'])

    read = es._read_email(uid="17", folder="Sent")

    assert read["_folder"] == "[Gmail]/Gesendet"
    assert ("select", '"[Gmail]/Gesendet"', True) in conn.calls
    assert conn.calls[-1] == ("logout",)


@pytest.mark.parametrize("operation, extra", [
    ("_read_email", {}),
    ("_reply_to_email", {"body": "Reply"}),
    ("_draft_reply_to_email", {"body": "Reply"}),
    ("_download_attachment", {"index": 0}),
])
def test_read_source_select_failure_never_fetches_or_sends(monkeypatch, operation, extra):
    conn = connect(monkeypatch, [b'(\\Sent) "/" "[Gmail]/Gesendet"'], select_status="NO")
    monkeypatch.setattr(es, "_send_email", lambda **kwargs: pytest.fail("Unexpected send"))
    monkeypatch.setattr(es, "_create_email_draft_document", lambda **kwargs: pytest.fail("Unexpected draft"))

    result = getattr(es, operation)(uid="17", folder="Sent", **extra)

    assert result == {"error": "IMAP folder not found: [Gmail]/Gesendet"}
    assert not any(call[0] == "uid" for call in conn.calls)
    assert conn.calls[-1] == ("logout",)


def test_draft_source_metadata_uses_resolved_folder_without_writing(monkeypatch):
    conn = connect(monkeypatch, [b'(\\Sent) "/" "[Gmail]/Gesendet"'])
    monkeypatch.setattr(es, "_create_email_draft_document", lambda **kwargs: kwargs)

    result = es._draft_reply_to_email(uid="17", body="Reply", folder="Sent")

    assert result["source_folder"] == "[Gmail]/Gesendet"
    assert result["source_uid"] == "17"
    assert conn.calls[-1] == ("logout",)


@pytest.mark.asyncio
async def test_list_tool_exposes_actual_folder_for_followup_reads(monkeypatch):
    connect(monkeypatch, [b'(\\Sent) "/" "[Gmail]/Gesendet"'])
    monkeypatch.setattr(es, "_read_accounts_from_db", lambda: [])
    monkeypatch.setattr(es, "_list_accounts_raw", lambda: [])

    content = await es.call_tool("list_emails", {"folder": "Sent"})

    assert "Folder: [Gmail]/Gesendet" in content[0].text
    assert "UID: 17" in content[0].text
