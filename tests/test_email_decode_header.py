"""Regression tests for routes.email_helpers._decode_header.

A single email whose Subject/From/To/Cc header declares an unknown or invalid
MIME charset (e.g. `=?x-unknown-charset?B?...?=`, common in spam/malformed mail)
used to raise an uncaught LookupError, because `bytes.decode(..., errors="replace")`
only handles byte-decode errors — not codec *lookup* failures. That crash
propagated into the inbox list endpoint, message fetch, and the background mail
pollers (routes/email_routes.py, routes/email_pollers.py, src/builtin_actions.py),
so one bad message could take down the whole inbox render / poller loop.

These pin the fallback so a bogus charset degrades gracefully to utf-8.
"""
import os
import tempfile
from pathlib import Path

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus_decode_hdr_"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes.email_helpers import _decode_header


def test_unknown_charset_does_not_raise():
    # The regression: an unknown codec name must not raise LookupError.
    assert _decode_header("=?x-unknown-charset?B?aGVsbG8=?=") == "hello"


def test_invalid_charset_falls_back_to_utf8():
    # A made-up charset on non-ASCII bytes should still produce a string.
    raw = "=?totally-bogus?Q?caf=C3=A9?="
    out = _decode_header(raw)
    assert isinstance(out, str)
    assert "caf" in out


def test_valid_utf8_unchanged():
    assert _decode_header("=?utf-8?B?SGVsbG8gV29ybGQ=?=") == "Hello World"


def test_valid_iso8859_1_unchanged():
    assert _decode_header("=?iso-8859-1?Q?caf=E9?=") == "café"


def test_plain_ascii_passthrough():
    assert _decode_header("Just a subject") == "Just a subject"


def test_empty_and_none():
    assert _decode_header("") == ""
    assert _decode_header(None) == ""
