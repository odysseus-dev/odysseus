"""AI reply context must search the account's real Sent/Archive/Drafts (#6249).

`_fetch_sender_thread_context` and `_pre_retrieve_context` used to loop over
the hardcoded names "INBOX", "Sent", "Archive", "Drafts". Gmail exposes those
mailboxes as "[Gmail]/Sent Mail", "[Gmail]/All Mail" and "[Gmail]/Drafts", so
every SELECT except INBOX failed and reply drafting only ever saw INBOX.
"""
from pathlib import Path

from routes.email import email_helpers as helpers
from routes.email import email_routes as routes_module


GMAIL_LIST = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
    b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
    b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Drafts"',
    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
    b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
    b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
]


class _FakeGmail:
    """Minimal imaplib stand-in: only mailboxes in LIST can be selected."""

    def __init__(self, list_lines=GMAIL_LIST):
        self.list_lines = list_lines
        self.selected = []
        self.logouts = 0

    def list(self, *args, **kwargs):
        return "OK", list(self.list_lines)

    def select(self, mailbox, readonly=False):
        name = mailbox[1:-1] if mailbox.startswith('"') else mailbox
        names = {line.rsplit(b'"', 2)[-2].decode() for line in self.list_lines}
        if name not in names:
            return "NO", [b"[NONEXISTENT] Unknown Mailbox"]
        self.selected.append(name)
        return "OK", [b"0"]

    def search(self, charset, *criteria):
        return "OK", [b""]

    def fetch(self, *args, **kwargs):  # pragma: no cover - search finds nothing
        return "NO", []

    def close(self):
        pass

    def logout(self):
        self.logouts += 1


GMAIL_EXPECTED = ["INBOX", "[Gmail]/Sent Mail", "[Gmail]/All Mail", "[Gmail]/Drafts"]


def test_reply_context_folders_resolve_gmail_special_use():
    assert helpers._reply_context_folders(_FakeGmail()) == GMAIL_EXPECTED


def test_reply_context_folders_fall_back_to_generic_names():
    class _NoList(_FakeGmail):
        def list(self, *args, **kwargs):
            return "NO", []

    assert helpers._reply_context_folders(_NoList()) == ["INBOX", "Sent", "Archive", "Drafts"]


def test_fetch_sender_thread_context_selects_gmail_folders(monkeypatch):
    conn = _FakeGmail()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: conn)

    assert helpers._fetch_sender_thread_context("sender@example.com") == ""

    assert conn.selected == GMAIL_EXPECTED
    assert conn.logouts == 1


def _known_sender(monkeypatch):
    class _KnownSender:
        """`_imap()` context for the known-sender check: sender has history."""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def select(self, *args, **kwargs):
            return "OK", [b"1"]

        def search(self, *args, **kwargs):
            return "OK", [b"1"]

    monkeypatch.setattr(helpers, "_imap", lambda *a, **kw: _KnownSender())
    # Keep the shared CardDAV contacts out of it; IMAP history decides.
    import src.tool_security as tool_security
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda owner: False)


def test_pre_retrieve_context_searches_gmail_folders(monkeypatch):
    ctx_conn = _FakeGmail()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: ctx_conn)
    _known_sender(monkeypatch)

    helpers._pre_retrieve_context(
        body="Following up on Project Alpha",
        sender="Known Sender <known@example.com>",
        owner="someone@example.com",
    )

    assert ctx_conn.selected == GMAIL_EXPECTED


def test_pre_retrieve_context_skips_all_mail_copies(monkeypatch):
    """Gmail's All Mail repeats INBOX messages; a copy must not fill the context."""
    raw = (
        b"Message-ID: <same@example.com>\r\n"
        b"From: Known Sender <known@example.com>\r\n"
        b"Subject: Budget review\r\n\r\n"
        b"Notes on Project Alpha\r\n"
    )

    class _SameMessageTwice(_FakeGmail):
        def search(self, charset, *criteria):
            found = self.selected[-1] in ("INBOX", "[Gmail]/All Mail")
            return "OK", [b"1" if found else b""]

        def fetch(self, *args, **kwargs):
            return "OK", [(b"1 (RFC822 {1}", raw), b")"]

    ctx_conn = _SameMessageTwice()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: ctx_conn)
    _known_sender(monkeypatch)

    snippets, terms = helpers._pre_retrieve_context(
        body="Following up on Project Alpha",
        sender="Known Sender <known@example.com>",
        owner="someone@example.com",
    )

    matches = [snippet for snippet in snippets if "Subject: Budget review" in snippet]
    assert terms and len(matches) == len(terms)


def test_message_being_answered_does_not_return_through_all_mail(monkeypatch):
    """exclude_uid names the INBOX copy; All Mail holds the same message under another UID."""
    raw = (
        b"Message-ID: <answering@example.com>\r\n"
        b"From: Known Sender <known@example.com>\r\n"
        b"Subject: Question\r\n\r\n"
        b"The message being answered\r\n"
    )

    class _AnsweredMessage(_FakeGmail):
        def search(self, charset, *criteria):
            found = self.selected[-1] in ("INBOX", "[Gmail]/All Mail")
            return "OK", [b"7" if found else b""]

        def fetch(self, *args, **kwargs):
            return "OK", [(b"7 (RFC822 {1}", raw), b")"]

        def uid(self, command, uid, query):
            assert (command, uid) == ("FETCH", "42")
            return "OK", [(b"7 (UID 42 BODY[HEADER.FIELDS (MESSAGE-ID)] {1}", raw), b")"]

    conn = _AnsweredMessage()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: conn)

    assert helpers._fetch_sender_thread_context("known@example.com", exclude_uid="42") == ""



def test_malformed_message_id_keeps_the_rest_of_the_context(monkeypatch):
    raw = (
        b"Message-ID: <caf\xe9@example.com>\r\n"
        b"From: Known Sender <known@example.com>\r\n"
        b"Subject: Question\r\n\r\n"
        b"Readable body\r\n"
    )

    class _RawHeader(_FakeGmail):
        def search(self, charset, *criteria):
            return "OK", [b"1" if self.selected[-1] == "INBOX" else b""]

        def fetch(self, *args, **kwargs):
            return "OK", [(b"1 (RFC822 {1}", raw), b")"]

    conn = _RawHeader()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: conn)

    assert "Readable body" in helpers._fetch_sender_thread_context("known@example.com")
    assert conn.selected == GMAIL_EXPECTED



def test_all_mail_copies_do_not_use_up_the_archive_slots(monkeypatch):
    def message(name):
        return (
            f"Message-ID: <{name}@example.com>\r\n"
            "From: Known Sender <known@example.com>\r\n"
            f"Subject: {name}\r\n\r\n"
            "Notes on Project Alpha\r\n"
        ).encode()

    mailboxes = {
        "INBOX": ["new-1", "new-2"],
        "[Gmail]/All Mail": ["old-1", "old-2", "new-1", "new-2"],
    }

    class _ArchiveBehindCopies(_FakeGmail):
        def search(self, charset, *criteria):
            names = mailboxes.get(self.selected[-1], [])
            return "OK", [" ".join(str(i + 1) for i in range(len(names))).encode()]

        def fetch(self, uid, query):
            name = mailboxes[self.selected[-1]][int(uid) - 1]
            return "OK", [(b"1 (RFC822 {1}", message(name)), b")"]

    ctx_conn = _ArchiveBehindCopies()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: ctx_conn)
    _known_sender(monkeypatch)

    snippets, _terms = helpers._pre_retrieve_context(
        body="Following up on Project Alpha",
        sender="Known Sender <known@example.com>",
        owner="someone@example.com",
    )

    assert any("Subject: old-2" in snippet for snippet in snippets)


def test_ai_reply_context_uses_the_account_being_answered():
    source = Path(routes_module.__file__).read_text(encoding="utf-8")

    assert "_pre_retrieve_context, original_body, to, account_id=account_id, owner=owner" in source
    assert "limit=3,\n                        account_id=account_id,\n                        owner=owner," in source
