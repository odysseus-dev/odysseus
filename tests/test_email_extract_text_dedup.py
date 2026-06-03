"""_extract_text must not duplicate the body when HTML precedes plain.

The HTML branch fired when `not text_parts` (no plain seen YET). If the
text/html part appears before the text/plain part in walk() order, the HTML
text was appended and then the plain part too, returning the body twice.
Plain is now always preferred; HTML is only a fallback when no plain exists.
"""
import email

import pytest


@pytest.fixture
def extract_text(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    import routes.email_helpers as eh
    return eh._extract_text


def _multipart(parts):
    # parts: list of (subtype, body) in the desired walk order
    raw = b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
    for sub, body in parts:
        raw += b'--B\r\nContent-Type: text/' + sub.encode() + b'; charset="utf-8"\r\n\r\n'
        raw += body.encode() + b'\r\n'
    raw += b'--B--\r\n'
    return email.message_from_bytes(raw)


def test_html_before_plain_not_duplicated(extract_text):
    msg = _multipart([("html", "<p>HELLO BODY</p>"), ("plain", "HELLO BODY")])
    out = extract_text(msg)
    assert out.count("HELLO BODY") == 1
    assert out.strip() == "HELLO BODY"  # plain preferred


def test_plain_before_html_still_plain(extract_text):
    msg = _multipart([("plain", "PLAIN ONE"), ("html", "<p>HTML TWO</p>")])
    assert extract_text(msg).strip() == "PLAIN ONE"


def test_html_only_falls_back(extract_text):
    msg = _multipart([("html", "<p>ONLY HTML</p>")])
    assert "ONLY HTML" in extract_text(msg)
