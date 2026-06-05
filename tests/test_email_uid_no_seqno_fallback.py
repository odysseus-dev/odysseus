"""Email move/flag must never fall back to sequence-number IMAP ops (#1874 sibling).

`imaplib`'s plain `store()` / `copy()` operate on message SEQUENCE NUMBERS, not
UIDs. `_store_email_flag` / `_move_email_message` (used by the archive / delete /
move / mark endpoints) had an `else` fallback that, when `_uid_exists` returned
False, ran `conn.store(uid, …)` / `conn.copy(uid, …)` + `conn.expunge()` — i.e.
it flagged/copied whichever message occupied sequence position == the UID value
and then permanently expunged it. A stale cached UID (or a server whose UID
probe misbehaves) therefore deleted an unrelated email.

The fix fails safe: when the UID isn't present, return False (callers surface
"Email not found") and never touch a message by sequence number.

This is distinct from #1874, which fixes the auto-spam poller's `_imap_move` in
`routes/email_helpers.py`; this covers the user-facing endpoints in
`routes/email_routes.py`.
"""
import pytest

from routes import email_routes
from routes.email_routes import _store_email_flag, _move_email_message


class _FakeConn:
    """Records IMAP calls. `uid_present` controls the FETCH-UID probe result.

    The sequence-number commands (store/copy/expunge) raise if ever called —
    the whole point of the fix is that they must not be reached.
    """
    def __init__(self, uid_present, uid_move_ok=True):
        self.uid_present = uid_present
        self.uid_move_ok = uid_move_ok
        self.uid_calls = []
        self.seqno_calls = []

    def uid(self, command, *args):
        self.uid_calls.append((command.upper(), args))
        cmd = command.upper()
        if cmd == "FETCH":
            return ("OK", [b"1 (UID 5031)"] if self.uid_present else [])
        if cmd == "MOVE":
            return ("OK" if self.uid_move_ok else "NO", [b""])
        if cmd in ("COPY", "STORE"):
            return ("OK", [b""])
        return ("OK", [b""])

    # Sequence-number APIs — must never be used with a UID.
    def store(self, *a):
        self.seqno_calls.append(("store", a)); return ("OK", [b""])

    def copy(self, *a):
        self.seqno_calls.append(("copy", a)); return ("OK", [b""])

    def expunge(self, *a):
        self.seqno_calls.append(("expunge", a)); return ("OK", [b""])


@pytest.fixture(autouse=True)
def _no_folder_resolution(monkeypatch):
    # _move_email_message resolves the destination folder via the connection;
    # short-circuit it so the test focuses on the UID-vs-seqno behaviour.
    monkeypatch.setattr(email_routes, "_resolve_mail_folder", lambda conn, dest, role="": dest)


def test_store_flag_missing_uid_fails_safe():
    conn = _FakeConn(uid_present=False)
    assert _store_email_flag(conn, "5031", "\\Deleted", add=True) is False
    assert conn.seqno_calls == []  # never touched a message by sequence number


def test_move_missing_uid_fails_safe():
    conn = _FakeConn(uid_present=False)
    assert _move_email_message(conn, "5031", "Trash", role="trash") is False
    assert conn.seqno_calls == []  # no copy/store/expunge on a phantom seqno


def test_store_flag_present_uid_uses_uid_store():
    conn = _FakeConn(uid_present=True)
    assert _store_email_flag(conn, "5031", "\\Seen", add=True) is True
    assert any(c[0] == "STORE" for c in conn.uid_calls)
    assert conn.seqno_calls == []


def test_move_present_uid_uses_uid_move():
    conn = _FakeConn(uid_present=True, uid_move_ok=True)
    assert _move_email_message(conn, "5031", "Archive", role="archive") is True
    assert any(c[0] == "MOVE" for c in conn.uid_calls)
    assert conn.seqno_calls == []
