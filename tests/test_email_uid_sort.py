"""#5256 — mailbox list order must follow message date, not numeric UID."""

from datetime import datetime, timezone

from routes.email_routes import _internaldate_epoch_from_meta, _uids_newest_first


class _FakeConn:
    def __init__(self, fetch_responses=None, sort_response=None):
        self._fetch = list(fetch_responses or [])
        self._sort = sort_response

    def uid(self, cmd, *args):
        if cmd == "SORT":
            return self._sort if self._sort else ("NO", [None])
        if cmd == "FETCH":
            if self._fetch:
                return self._fetch.pop(0)
            return "NO", []
        raise AssertionError(f"unexpected uid command {cmd!r}")


def test_internaldate_epoch_prefers_internaldate():
    meta = b'1 (UID 1 INTERNALDATE "09-Jul-2026 12:00:00 +0000" RFC822.HEADER {2}'
    header = b"a\r\n"
    ep = _internaldate_epoch_from_meta(meta, header)
    expected = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert abs(ep - expected) < 1.0


def test_uids_newest_first_when_low_uid_is_newer():
    # Proton-style: UID 1 is newest, UID 3 is oldest — reversed() would be wrong.
    newer_meta = (
        b'1 (UID 1 INTERNALDATE "09-Jul-2026 14:00:00 +0000" RFC822.HEADER {40}',
        b"Date: Wed, 09 Jul 2026 14:00:00 +0000\r\nSubject: new\r\n\r\n",
    )
    older_meta = (
        b'2 (UID 3 INTERNALDATE "08-Jul-2026 10:00:00 +0000" RFC822.HEADER {41}',
        b"Date: Tue, 08 Jul 2026 10:00:00 +0000\r\nSubject: old\r\n\r\n",
    )
    conn = _FakeConn(
        sort_response=("NO", [None]),
        fetch_responses=[("OK", [newer_meta, older_meta])],
    )
    ordered = _uids_newest_first(conn, [b"3", b"1"])
    assert [u.decode() for u in ordered] == ["1", "3"]