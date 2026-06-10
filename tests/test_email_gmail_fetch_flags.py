"""Regression tests for _group_uid_fetch_records (Gmail FLAGS placement).

imaplib hands back UID FETCH responses as an interleaved list of
``(meta, literal)`` tuples and bare ``bytes`` elements. Dovecot sends FLAGS
before the RFC822.HEADER literal, so they sit inside the tuple meta; Gmail
sends FLAGS *after* the literal, as a bare ``b' FLAGS (\\Seen))'`` element.
The old grouping loop only looked at tuples, so on Gmail every message lost
its FLAGS and rendered as unread/unflagged in the email library.
"""

import re

from routes.email_routes import _group_uid_fetch_records, _uid_from_fetch_meta


def _flags(meta_b: bytes) -> str:
    m = re.search(rb"FLAGS \(([^)]*)\)", meta_b)
    return m.group(1).decode() if m else ""


# Captured shape of a real Gmail response to
# UID FETCH a,b (UID FLAGS RFC822.HEADER RFC822.SIZE):
GMAIL_RESPONSE = [
    (b"10779 (UID 18723 RFC822.SIZE 54308 RFC822.HEADER {24}", b"Subject: read one\r\n\r\n"),
    rb" FLAGS (\Seen))",
    (b"10780 (UID 18724 RFC822.SIZE 124310 RFC822.HEADER {26}", b"Subject: unread one\r\n\r\n"),
    rb" FLAGS ())",
]

# Dovecot puts FLAGS before the literal and terminates with a bare b')'.
DOVECOT_RESPONSE = [
    (rb"1 (UID 5 FLAGS (\Seen) RFC822.SIZE 100 RFC822.HEADER {18}", b"Subject: hi\r\n\r\n"),
    b")",
    (b"2 (UID 6 FLAGS () RFC822.SIZE 90 RFC822.HEADER {19}", b"Subject: new\r\n\r\n"),
    b")",
]


def test_gmail_post_literal_flags_attach_to_their_own_message():
    grouped = _group_uid_fetch_records(GMAIL_RESPONSE)

    assert len(grouped) == 2
    assert _uid_from_fetch_meta(grouped[0][0]) == "18723"
    assert _flags(grouped[0][0]) == r"\Seen"
    assert grouped[0][1] == b"Subject: read one\r\n\r\n"

    assert _uid_from_fetch_meta(grouped[1][0]) == "18724"
    assert _flags(grouped[1][0]) == ""
    assert grouped[1][1] == b"Subject: unread one\r\n\r\n"


def test_dovecot_pre_literal_flags_unchanged():
    grouped = _group_uid_fetch_records(DOVECOT_RESPONSE)

    assert len(grouped) == 2
    assert _flags(grouped[0][0]) == r"\Seen"
    assert _flags(grouped[1][0]) == ""
    assert grouped[1][1] == b"Subject: new\r\n\r\n"


def test_size_and_uid_survive_grouping():
    grouped = _group_uid_fetch_records(GMAIL_RESPONSE)
    sizes = [re.search(rb"RFC822\.SIZE (\d+)", m).group(1) for m, _ in grouped]
    assert sizes == [b"54308", b"124310"]


def test_empty_and_none_inputs():
    assert _group_uid_fetch_records(None) == []
    assert _group_uid_fetch_records([]) == []
    # A stray bare element before any tuple opens no record and must not crash.
    assert _group_uid_fetch_records([rb" FLAGS (\Seen))"]) == []
