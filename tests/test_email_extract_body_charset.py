"""Regression tests for routes.email_helpers._extract_text / _extract_html.

The body-decode path used the same unsafe pattern that `_decode_header` was
already fixed for: `payload.decode(charset, errors="replace")`. `errors="replace"`
only handles byte-level decode errors — an *unknown codec name* (e.g. a
malformed `Content-Type: text/plain; charset="x-unknown-charset"`, common in
spam / misconfigured senders) raises `LookupError` before the error handler is
ever consulted.

That LookupError propagated out of `_extract_text`/`_extract_html`, and the
read-email route catches it and returns the generic {"error": "Mail operation
failed"} — so a single message with a bogus charset label became completely
unreadable in the UI, and broke the background summarization poller for that
message.

These pin the fallback so a bogus body charset degrades gracefully to utf-8.
"""
import os
import email as email_mod
import tempfile
from pathlib import Path

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus_extract_body_"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes.email_helpers import _extract_text, _extract_html, _safe_decode_payload


def _msg(raw: bytes):
    return email_mod.message_from_bytes(raw)


def test_extract_text_unknown_charset_does_not_raise():
    msg = _msg(
        b'From: a@b.com\r\n'
        b'Subject: t\r\n'
        b'Content-Type: text/plain; charset="x-unknown-charset"\r\n'
        b'\r\n'
        b'hello body\r\n'
    )
    out = _extract_text(msg)
    assert isinstance(out, str)
    assert "hello body" in out


def test_extract_html_unknown_charset_does_not_raise():
    msg = _msg(
        b'From: a@b.com\r\n'
        b'Subject: t\r\n'
        b'Content-Type: text/html; charset="totally-made-up"\r\n'
        b'\r\n'
        b'<p>hi there</p>\r\n'
    )
    out = _extract_html(msg)
    assert isinstance(out, str)
    assert "hi there" in out


def test_extract_text_from_html_part_unknown_charset():
    # multipart/alternative with only a text/html part: _extract_text routes it
    # through the html-fallback branch that strips tags + unescapes entities.
    msg = _msg(
        b'From: a@b.com\r\n'
        b'Subject: t\r\n'
        b'MIME-Version: 1.0\r\n'
        b'Content-Type: multipart/alternative; boundary="BB"\r\n'
        b'\r\n'
        b'--BB\r\n'
        b'Content-Type: text/html; charset="bogus-codec"\r\n'
        b'\r\n'
        b'<p>body&amp;more</p>\r\n'
        b'--BB--\r\n'
    )
    out = _extract_text(msg)
    assert isinstance(out, str)
    assert "body&more" in out  # tags stripped, entities unescaped


def test_extract_text_valid_utf8_unchanged():
    msg = _msg(
        b'Content-Type: text/plain; charset="utf-8"\r\n'
        b'Content-Transfer-Encoding: 8bit\r\n'
        b'\r\n'
        + "café\r\n".encode("utf-8")
    )
    assert "café" in _extract_text(msg)


def test_safe_decode_payload_fallback_and_none():
    assert _safe_decode_payload(b"x", "no-such-codec") == "x"
    assert _safe_decode_payload(b"x", None) == "x"
    assert _safe_decode_payload(None, "utf-8") == ""
