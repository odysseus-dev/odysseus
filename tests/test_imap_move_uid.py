"""_imap_move must address messages by UID, not sequence number.

The auto-spam poller passes a real IMAP UID (from conn.uid("SEARCH", ...))
to _imap_move, but the function used conn.copy()/conn.store(), which operate
on message SEQUENCE NUMBERS. So a UID like 90521 was interpreted as sequence
number 90521 — moving/deleting the wrong message or silently no-oping. It
must use the UID commands.
"""
import sys
import types

import pytest


@pytest.fixture
def email_helpers(monkeypatch, tmp_path):
    # Keep _init_scheduled_db (run at import) off the real data dir.
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    import routes.email_helpers as eh
    return eh


class _FakeIMAP:
    def __init__(self):
        self.calls = []

    def select(self, mbox):
        self.calls.append(("select", mbox)); return ("OK", [b""])

    def copy(self, *a):
        self.calls.append(("copy",) + a); return ("OK", [b""])

    def store(self, *a):
        self.calls.append(("store",) + a); return ("OK", [b""])

    def uid(self, *a):
        self.calls.append(("uid",) + a); return ("OK", [b""])

    def expunge(self):
        self.calls.append(("expunge",)); return ("OK", [b""])

    def logout(self):
        pass


def test_move_uses_uid_commands_not_seqnum(email_helpers, monkeypatch):
    fake = _FakeIMAP()
    monkeypatch.setattr(email_helpers, "_imap_connect", lambda *a, **k: fake)
    ok = email_helpers._imap_move(b"90521", "Spam", src="INBOX")
    assert ok is True
    verbs = [c[0] for c in fake.calls]
    uid_ops = [c[1] for c in fake.calls if c[0] == "uid"]
    assert "COPY" in uid_ops and "STORE" in uid_ops
    # the sequence-number commands must NOT be used to address a UID
    assert "copy" not in verbs
    assert "store" not in verbs
