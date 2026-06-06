"""Regression for issue #1961 — read_email (and reply_to_email,
download_attachment) failed on iCloud IMAP accounts.

iCloud's IMAP server silently ignores the legacy bare `RFC822` fetch item: a
`UID FETCH <uid> (RFC822)` returns status OK but only `(UID <uid>)` with no body
tuple, so the parse treats the message as "not found" — even though list_emails
works (it uses `RFC822.HEADER`, which iCloud honours). The modern
`BODY.PEEK[]` item is honoured by iCloud and Gmail alike and doesn't set \\Seen.

The fix is an IMAP-protocol-string change exercised only against a live server,
so it's guarded at the source here (per CONTRIBUTING's "guard at source" note):
the three full-message fetches must use BODY.PEEK[], and no bare (RFC822) full
fetch may remain. The header/uid fetches must be left untouched so listing keeps
working.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "mcp_servers/email_server.py").read_text(encoding="utf-8")


def _full_fetches():
    # every conn.uid("FETCH", ..., "<item>") call's fetch item
    return re.findall(r'conn\.uid\(\s*"FETCH"\s*,[^,]+,\s*"([^"]+)"\s*\)', SRC)


def test_full_message_fetches_use_body_peek_not_bare_rfc822():
    items = _full_fetches()
    assert items, "no conn.uid FETCH calls found — test anchor stale"
    # No bare (RFC822) full-message fetch may remain (it breaks iCloud).
    assert "(RFC822)" not in items, f"a bare (RFC822) full fetch remains: {items}"
    # The full-message reads now use BODY.PEEK[] — at least the 3 known sites.
    assert items.count("(BODY.PEEK[])") >= 3, f"expected >=3 BODY.PEEK[] fetches: {items}"


def test_header_and_uid_fetches_preserved():
    items = _full_fetches()
    # Listing relies on RFC822.HEADER (iCloud honours it) — must stay.
    assert "(RFC822.HEADER)" in items, "RFC822.HEADER fetch (used by listing) must be preserved"
    # UID-only probes must stay as-is.
    assert "(UID)" in items, "(UID) probe fetch must be preserved"
