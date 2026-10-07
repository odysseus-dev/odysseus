"""Tests for filing a copy of outgoing mail in the Sent folder.

Two bugs lived here and both made mail sent from Odysseus invisible inside
Odysseus while the recipient received it fine:

- `imaplib` passes the mailbox name to `APPEND` verbatim, so the unquoted
  name broke on every provider whose Sent folder has a space in it —
  Exchange Online's `Sent Items` and Gmail's `[Gmail]/Sent Mail`. The server
  rejected the command and the failure was swallowed by a bare `except`.
- Exchange Online and Gmail file their own copy of an SMTP-submitted
  message, so appending unconditionally gives those accounts two identical
  entries once the quoting is fixed.

`_ensure_sent_copy` resolves both: look for the server's own copy first,
append only when none shows up, and always quote the mailbox.
"""

import unittest.mock as mock

import pytest

from routes.email_helpers import (
    _ensure_sent_copy,
    _find_message_uid,
    _server_saves_sent_copy,
)


class FakeIMAP:
    """Minimal IMAP double that records the raw mailbox argument it is given.

    `select`/`append` capture their mailbox verbatim so a test can assert the
    name went out quoted; `search_hits` drives what SEARCH reports back.
    """

    def __init__(self, search_hits=None, append_status="OK", strict_mailbox=False):
        self.search_hits = list(search_hits or [])
        self.append_status = append_status
        self.strict_mailbox = strict_mailbox
        self.appended = []
        self.selected = []
        self.searches = []

    def _check(self, mailbox):
        # A real server parses the mailbox as a single astring: an unquoted
        # name containing a space is a syntax error, not a missing folder.
        if self.strict_mailbox and " " in mailbox and not mailbox.startswith('"'):
            raise RuntimeError(f"BAD: unparsable mailbox {mailbox!r}")

    def select(self, mailbox, readonly=False):
        self._check(mailbox)
        self.selected.append(mailbox)
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        assert cmd == "SEARCH"
        self.searches.append(args[-1])
        hit = self.search_hits.pop(0) if self.search_hits else None
        return ("OK", [hit.encode() if hit else b""])

    def append(self, mailbox, flags, date_time, message):
        self._check(mailbox)
        self.appended.append((mailbox, flags, message))
        return self.append_status, [b"[APPENDUID 12 99]"]


# ── Mailbox quoting ───────────────────────────────────────────────

@pytest.mark.parametrize("folder", ["Sent Items", "[Gmail]/Sent Mail", "Sent"])
def test_append_quotes_the_mailbox_name(folder):
    """The regression: a space in the folder name must not break APPEND."""
    imap = FakeIMAP(strict_mailbox=True)
    uid, appended = _ensure_sent_copy(imap, folder, "<m@x>", b"raw")
    assert appended is True
    assert uid == "99"
    assert imap.appended[0][0] == f'"{folder}"'


def test_select_quotes_the_mailbox_name():
    imap = FakeIMAP(strict_mailbox=True)
    _find_message_uid(imap, "Sent Items", "<m@x>")
    assert imap.selected == ['"Sent Items"']


def test_append_failure_is_raised_not_swallowed():
    """A rejected APPEND must surface so the caller can log which folder."""
    imap = FakeIMAP(append_status="NO")
    with pytest.raises(RuntimeError, match="APPEND to Sent Items failed"):
        _ensure_sent_copy(imap, "Sent Items", "<m@x>", b"raw")


# ── Deduplication against the server's own copy ───────────────────

def test_server_copy_is_reused_instead_of_appending():
    """Exchange/Gmail already filed it — appending would make a duplicate."""
    imap = FakeIMAP(search_hits=["41 42"])
    uid, appended = _ensure_sent_copy(
        imap, "Sent Items", "<m@x>", b"raw", server_saves_copy=True,
    )
    assert (uid, appended) == ("42", False)
    assert imap.appended == []


def test_append_happens_when_the_server_files_no_copy():
    """A plain IMAP server saves nothing, so Odysseus must append its own."""
    imap = FakeIMAP(search_hits=[None])
    uid, appended = _ensure_sent_copy(imap, "Sent", "<m@x>", b"raw")
    assert appended is True
    assert len(imap.appended) == 1


def test_auto_save_provider_is_retried_before_appending():
    """The server-side copy lands a beat after SMTP returns, so wait for it."""
    imap = FakeIMAP(search_hits=[None, None, "7"])
    with mock.patch("routes.email_helpers.time.sleep") as slept:
        uid, appended = _ensure_sent_copy(
            imap, "Sent Items", "<m@x>", b"raw",
            server_saves_copy=True, wait_seconds=0.01,
        )
    assert (uid, appended) == ("7", False)
    assert imap.appended == []
    assert slept.call_count == 2


def test_plain_imap_is_not_delayed_by_retries():
    """No auto-save means no reason to make every send wait."""
    imap = FakeIMAP(search_hits=[None])
    with mock.patch("routes.email_helpers.time.sleep") as slept:
        _ensure_sent_copy(imap, "Sent", "<m@x>", b"raw", server_saves_copy=False)
    assert slept.call_count == 0


def test_append_still_happens_if_auto_save_never_materializes():
    """Better a late copy than none: fall back to APPEND after the grace period."""
    imap = FakeIMAP(search_hits=[None, None, None, None])
    with mock.patch("routes.email_helpers.time.sleep"):
        uid, appended = _ensure_sent_copy(
            imap, "Sent Items", "<m@x>", b"raw", server_saves_copy=True,
        )
    assert appended is True
    assert uid == "99"


# ── Message-ID handling ───────────────────────────────────────────

def test_message_id_is_searched_without_angle_brackets():
    imap = FakeIMAP(search_hits=["5"])
    _find_message_uid(imap, "Sent", "<abc@host>")
    assert imap.searches == ['HEADER Message-ID "abc@host"']


def test_quotes_in_a_message_id_cannot_break_out_of_the_search_term():
    imap = FakeIMAP(search_hits=["5"])
    _find_message_uid(imap, "Sent", '<a"b@host>')
    assert imap.searches == ['HEADER Message-ID "a\\"b@host"']


def test_missing_message_id_skips_the_search_and_appends():
    """Without an anchor there is nothing to dedupe against, so just append."""
    imap = FakeIMAP()
    uid, appended = _ensure_sent_copy(imap, "Sent", "", b"raw", server_saves_copy=True)
    assert appended is True
    assert imap.searches == []


def test_search_failure_does_not_abort_the_send():
    imap = FakeIMAP()
    imap.select = mock.Mock(side_effect=RuntimeError("connection reset"))
    assert _find_message_uid(imap, "Sent", "<m@x>") is None


# ── Provider detection ────────────────────────────────────────────

@pytest.mark.parametrize("cfg", [
    {"oauth_provider": "microsoft"},
    {"oauth_provider": "google"},
    {"oauth_provider": "MICROSOFT"},
    {"smtp_host": "smtp.office365.com"},
    {"smtp_host": "SMTP.GMAIL.COM"},
    {"smtp_host": "smtp-mail.outlook.com"},
])
def test_hosted_providers_are_known_to_save_their_own_copy(cfg):
    assert _server_saves_sent_copy(cfg) is True


@pytest.mark.parametrize("cfg", [
    {},
    None,
    {"smtp_host": "mail.example.com"},
    {"oauth_provider": ""},
    {"oauth_provider": "dropbox", "smtp_host": "smtp.fastmail.com"},
])
def test_self_hosted_servers_are_not_assumed_to_save_a_copy(cfg):
    assert _server_saves_sent_copy(cfg) is False
