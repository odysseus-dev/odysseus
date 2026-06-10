"""read_email must not crash on a message with an unknown charset label.

_extract_text decoded body parts with payload.decode(charset,
errors="replace"). errors="replace" only handles decode errors, not an
unregistered codec NAME, so a part declaring charset="x-unknown-broken"
(common in spam/broken senders) raised LookupError. _read_email calls
_extract_text with no try/except, so the whole read_email MCP tool failed.
"""
import email

import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


def test_unknown_charset_plain_does_not_crash():
    msg = email.message_from_bytes(
        b'Content-Type: text/plain; charset="x-unknown-broken"\r\n\r\nhello body\r\n'
    )
    out = es._extract_text(msg)
    assert isinstance(out, str)
    assert "hello body" in out


def test_unknown_charset_multipart_html_does_not_crash():
    raw = (
        b'Content-Type: multipart/alternative; boundary="B"\r\n\r\n'
        b'--B\r\nContent-Type: text/html; charset="bogus-codec"\r\n\r\n'
        b'<p>Hi&amp;bye</p>\r\n--B--\r\n'
    )
    msg = email.message_from_bytes(raw)
    out = es._extract_text(msg)
    assert isinstance(out, str)


def test_known_charset_still_decodes():
    msg = email.message_from_bytes(
        b'Content-Type: text/plain; charset="utf-8"\r\n\r\ncaf\xc3\xa9\r\n'
    )
    assert "café" in es._extract_text(msg)
