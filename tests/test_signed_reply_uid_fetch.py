"""prepare-signed-reply must fetch the source email by UID, not sequence number.

/api/email/attachment-as-doc stores a real IMAP UID (from a UID FETCH) in
Document.source_email_uid. The signed-reply header fetch addressed it with
conn.fetch(), which operates on message SEQUENCE numbers. After any deletion
in the mailbox those numberings diverge, so the reply draft was silently
addressed to the wrong sender with the wrong subject and threading — the
same class of bug as the move/flag fix in #2732.
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


HEADER_BYTES = (
    b"From: Alice Example <alice@example.com>\r\n"
    b"Subject: Contract to sign\r\n"
    b"Message-ID: <msg-123@example.com>\r\n"
    b"References: <thread-a@example.com>\r\n"
    b"\r\n"
)


class _FakeIMAP:
    """Records verbs; answers UID FETCH with a real header payload."""

    def __init__(self):
        self.calls = []

    def select(self, mbox, readonly=False):
        self.calls.append(("select", mbox, readonly))
        return ("OK", [b"1"])

    def fetch(self, *a):  # sequence-number path — must never be hit
        self.calls.append(("fetch",) + a)
        return ("OK", [(b"1 (RFC822.HEADER {2}", b"\r\n"), b")"])

    def uid(self, *a):
        self.calls.append(("uid",) + a)
        return ("OK", [(b"1 (UID 90521 RFC822.HEADER {123}", HEADER_BYTES), b")"])


@pytest.fixture
def doc_routes(monkeypatch, tmp_path):
    # Keep email_helpers' import-time DB init off the real data dir.
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    import routes.document_routes as dr
    return dr


def _doc(**over):
    base = dict(
        source_email_uid="90521",
        source_email_folder="INBOX",
        source_email_account_id="",
        source_email_message_id="<stored@example.com>",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _patch_imap(monkeypatch, conn):
    import routes.email_routes as er

    @contextmanager
    def _fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(er, "_imap", _fake_imap)


def test_header_fetch_uses_uid_command_not_seqnum(doc_routes, monkeypatch):
    fake = _FakeIMAP()
    _patch_imap(monkeypatch, fake)

    headers = doc_routes._fetch_source_reply_headers(_doc())

    uid_ops = [c for c in fake.calls if c[0] == "uid"]
    assert uid_ops == [("uid", "FETCH", b"90521", "(RFC822.HEADER)")]
    # the sequence-number FETCH must NOT be used to address a UID
    assert all(c[0] != "fetch" for c in fake.calls)

    assert headers["to"] == "alice@example.com"
    assert headers["to_name"] == "Alice Example"
    assert headers["subject"] == "Re: Contract to sign"
    assert headers["in_reply_to"] == "<msg-123@example.com>"
    assert headers["references"] == "<thread-a@example.com> <msg-123@example.com>"


def test_header_fetch_failure_keeps_stored_message_id(doc_routes, monkeypatch):
    class _Boom:
        def select(self, *a, **k):
            raise RuntimeError("account offline")

    _patch_imap(monkeypatch, _Boom())

    headers = doc_routes._fetch_source_reply_headers(_doc())

    assert headers["to"] == ""
    assert headers["to_name"] == ""
    assert headers["subject"] == ""
    assert headers["in_reply_to"] == "<stored@example.com>"
    assert headers["references"] == "<stored@example.com>"
