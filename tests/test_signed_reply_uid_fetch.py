"""prepare-signed-reply must read the source email by IMAP UID (#5456).

Documents created from an email attachment store the message's real IMAP UID
in ``source_email_uid``. The signed-reply flow read that value back with a
plain ``FETCH``, which addresses messages by sequence number. Once anything
older in the folder was expunged, the reply draft was prefilled with the
To/Subject/threading headers of a different email.
"""
from contextlib import contextmanager

import pytest


def _header(sender: str, subject: str, message_id: str, refs: str = "") -> bytes:
    lines = [f"From: {sender}", f"Subject: {subject}", f"Message-ID: {message_id}"]
    if refs:
        lines.append(f"References: {refs}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class _ExpungedMailbox:
    """Folder where UID 7 is the 3rd message: sequence number 7 is someone else."""

    def __init__(self):
        self.by_uid = {
            b"7": _header("Alice <alice@example.com>", "Contract", "<contract@example.com>",
                          "<thread-start@example.com>"),
        }
        self.by_seq = {
            b"7": _header("Mallory <mallory@example.com>", "Unrelated", "<other@example.com>"),
        }
        self.selected = None
        self.uid_calls = []

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.uid_calls.append((command,) + args)
        assert command == "FETCH"
        msg_set, query = args
        assert "RFC822.HEADER" in query
        raw = self.by_uid.get(msg_set)
        if raw is None:
            return "OK", [None]
        return "OK", [(b"1 (UID " + msg_set + b" RFC822.HEADER {%d}" % len(raw), raw), b")"]

    def fetch(self, msg_set, query):
        raw = self.by_seq.get(msg_set)
        if raw is None:
            return "OK", [None]
        return "OK", [(msg_set + b" (RFC822.HEADER {%d}" % len(raw), raw), b")"]


@pytest.fixture
def mailbox(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    import routes.email_routes as email_routes

    box = _ExpungedMailbox()
    opened = []

    @contextmanager
    def _fake_imap(account_id=None, owner=""):
        opened.append(account_id)
        yield box

    monkeypatch.setattr(email_routes, "_imap", _fake_imap)
    box.opened = opened
    return box


def test_signed_reply_headers_come_from_the_uid_not_the_sequence_number(mailbox):
    from routes.document.document_routes import _fetch_signed_reply_headers

    reply = _fetch_signed_reply_headers("acct-1", "INBOX", "7", "<contract@example.com>")

    assert mailbox.opened == ["acct-1"]
    assert mailbox.selected == '"INBOX"'
    assert mailbox.uid_calls == [("FETCH", b"7", "(RFC822.HEADER)")]
    assert reply["to"] == "alice@example.com"
    assert reply["to_name"] == "Alice"
    assert reply["subject"] == "Re: Contract"
    assert reply["in_reply_to"] == "<contract@example.com>"
    assert reply["references"] == "<thread-start@example.com> <contract@example.com>"


def test_missing_uid_keeps_the_stored_message_id_for_threading(mailbox):
    from routes.document.document_routes import _fetch_signed_reply_headers

    # UID 99 no longer exists; sequence number 99 does not exist either, but a
    # plain FETCH of a live sequence number would have returned a stranger.
    reply = _fetch_signed_reply_headers(None, "INBOX", "99", "<contract@example.com>")

    assert reply == {
        "to": "",
        "to_name": "",
        "subject": "",
        "in_reply_to": "<contract@example.com>",
        "references": "<contract@example.com>",
    }
