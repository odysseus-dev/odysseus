"""Regression for issue #1613 — on a large Gmail mailbox the email-summary
poller's `SEARCH ALL` fallback can time out mid-response, leaving its huge
`* SEARCH <uids…>` line unread on the socket. The next command (the downstream
re-select / EXAMINE) then reads those leftover bytes and fails with
`EXAMINE => unexpected response: b'325188 …'`.

`_latest_inbox_fallback_uids` reconnects on a failed SEARCH ALL so the downstream
command always runs on a clean socket. Tested with a fake IMAP connection — no
live server needed; reconnecting is correct by construction (a fresh connection
cannot carry the old one's leftover bytes).
"""
from routes import email_pollers as ep


class _FakeConn:
    def __init__(self, search_result=None, raise_on_search=False, name="orig"):
        self.name = name
        self._sr = search_result
        self._raise = raise_on_search
        self.selects = []
        self.logged_out = False

    def select(self, mailbox, readonly=False):
        self.selects.append(mailbox)
        return ("OK", [b""])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            if self._raise:
                raise OSError("timed out")
            return self._sr
        return ("OK", [None])

    def logout(self):
        self.logged_out = True


def test_fallback_success_keeps_conn_and_returns_latest_uids():
    conn = _FakeConn(search_result=("OK", [b"1 2 3 4 5 6 7 8 9 10 11 12"]))
    fresh = _FakeConn(name="fresh")
    uids, out = ep._latest_inbox_fallback_uids(conn, lambda: fresh)
    assert out is conn                       # no reconnect on success
    assert not conn.logged_out
    assert uids and all(f == "INBOX" for f, _ in uids)
    assert len(uids) <= 8                     # keeps only the latest few


def test_fallback_reconnects_on_poisoned_socket():
    conn = _FakeConn(raise_on_search=True)
    fresh = _FakeConn(name="fresh")
    calls = []

    def reconnect():
        calls.append(1)
        return fresh

    uids, out = ep._latest_inbox_fallback_uids(conn, reconnect)
    assert uids == []                         # failed scan yields nothing
    assert out is fresh                        # downstream uses a FRESH connection
    assert out is not conn                      # not the poisoned one
    assert calls == [1]                         # reconnected exactly once
    assert conn.logged_out                      # poisoned conn was closed


def test_fallback_empty_search_returns_no_uids_same_conn():
    conn = _FakeConn(search_result=("OK", [b""]))
    uids, out = ep._latest_inbox_fallback_uids(conn, lambda: _FakeConn(name="fresh"))
    assert uids == []
    assert out is conn
