"""Regression: check_outbound_url must reject a non-string URL, not crash.

The `if not url or not url.strip()` guard only handled falsy values; a truthy
non-string (e.g. an int) reached `.strip()` and raised AttributeError out of
this SSRF check. Non-strings now fail closed with a clear message.
"""
from src.url_safety import check_outbound_url


def test_non_string_fails_closed():
    ok, _ = check_outbound_url(123)
    assert ok is False
    ok2, _ = check_outbound_url(None)
    assert ok2 is False
