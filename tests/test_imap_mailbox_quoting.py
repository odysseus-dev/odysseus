"""Regression coverage for IMAP mailbox names that contain spaces.

imaplib does not quote mailbox arguments for SELECT/APPEND/MOVE/COPY, so callers
must quote names such as "[Gmail]/All Mail" or "Sent Items" themselves.
"""

from pathlib import Path

import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


class FakeListConn:
    def __init__(self):
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b""]
        return "OK", []

    def logout(self):
        self.calls.append(("logout",))


class FakeMoveConn:
    def __init__(self):
        self.calls = []

    def list(self):
        self.calls.append(("list",))
        return "OK", []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "FETCH":
            return "OK", [b"1 (UID 123)"]
        if command == "MOVE":
            return "NO", []
        return "OK", []

    def expunge(self):
        self.calls.append(("expunge",))

    def logout(self):
        self.calls.append(("logout",))


def test_mcp_list_emails_quotes_spaced_folder_on_select(monkeypatch):
    conn = FakeListConn()
    monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)

    assert es._list_emails(folder="Sent Items") == []

    assert conn.calls[0] == ("select", '"Sent Items"', True)


def test_mcp_quote_helper_handles_spaced_and_quoted_mailboxes():
    assert es._q("Sent Items") == '"Sent Items"'
    assert es._q('[Gmail]/All Mail') == '"[Gmail]/All Mail"'
    assert es._q('Label "Needs Reply"') == '"Label \\"Needs Reply\\""'


def test_mcp_quote_helper_encodes_non_ascii_to_modified_utf7():
    # A decoded Cyrillic name must go back over the wire as modified UTF-7 (#3514)
    # so the IMAP server can resolve it; ASCII names are unchanged (asserted above).
    assert es._q("Отправленные") == '"&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"'


def test_mcp_folder_name_from_list_line_decodes_modified_utf7():
    line = b'(\\HasNoChildren) "/" &BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-'
    assert es._folder_name_from_list_line(line) == "Отправленные"


def test_mcp_folder_name_round_trips_through_quote():
    # decode (for display) -> _q (re-encode for the wire) returns the wire form.
    line = b'(\\HasNoChildren) "/" &BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-'
    name = es._folder_name_from_list_line(line)
    assert es._q(name) == '"&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"'


def test_known_imap_mailbox_call_sites_are_quoted():
    mcp = Path("mcp_servers/email_server.py").read_text()
    assert "conn.select(folder" not in mcp
    assert "conn.select(source_folder" not in mcp
    assert "imap.append(sent_folder" not in mcp
    assert 'conn.uid("MOVE", _b(msg_set), dest_folder)' not in mcp
    assert 'conn.uid("COPY", _b(msg_set), dest_folder)' not in mcp
    assert 'conn.uid("MOVE", _b(uid), dest_folder)' not in mcp
    assert 'conn.uid("COPY", _b(uid), dest_folder)' not in mcp

    pollers = Path("routes/email_pollers.py").read_text()
    assert "conn.select(sent_name" not in pollers
    assert "imap.append(sent_folder" not in pollers

    document_routes = Path("routes/document_routes.py").read_text()
    assert "conn.select(doc.source_email_folder" not in document_routes


def test_mcp_move_message_quotes_destination_for_move_and_fallback_copy(monkeypatch):
    conn = FakeMoveConn()
    monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)

    assert es._move_message("123", "INBOX", "[Gmail]/All Mail") is True

    assert ("uid", "MOVE", b"123", '"[Gmail]/All Mail"') in conn.calls
    assert ("uid", "COPY", b"123", '"[Gmail]/All Mail"') in conn.calls


def test_mcp_bulk_move_quotes_destination_for_move_and_fallback_copy(monkeypatch):
    conn = FakeMoveConn()
    monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)

    assert es._bulk_move(["123"], "INBOX", "[Gmail]/All Mail") == 1

    assert ("uid", "MOVE", b"123", '"[Gmail]/All Mail"') in conn.calls
    assert ("uid", "COPY", b"123", '"[Gmail]/All Mail"') in conn.calls
