"""Behavioral guard for issue #158 — Microsoft basic-auth error normalization.

Modern Outlook / Office 365 mailboxes reject username+password IMAP/SMTP
logins with opaque errors ("535 5.7.139 Authentication unsuccessful, basic
authentication is disabled" or a bare "AUTHENTICATE failed"). Users read that
as a wrong password. friendly_auth_error turns it into an actionable message
on Microsoft hosts, while leaving every other error untouched.
"""
from routes.email_auth_hints import friendly_auth_error, is_microsoft_host


def test_microsoft_host_detection():
    assert is_microsoft_host("outlook.office365.com")
    assert is_microsoft_host("smtp-mail.outlook.com")
    assert is_microsoft_host("imap-mail.hotmail.com")
    assert is_microsoft_host("EAS.OUTLOOK.COM")  # case-insensitive
    assert not is_microsoft_host("imap.gmail.com")
    assert not is_microsoft_host("mail.fastmail.com")
    assert not is_microsoft_host("")


def test_microsoft_basic_auth_error_is_normalized():
    raw = "b'535 5.7.139 Authentication unsuccessful, basic authentication is disabled'"
    msg = friendly_auth_error("outlook.office365.com", raw)
    assert "basic authentication" in msg.lower()
    assert "OAuth" in msg or "Graph" in msg
    assert msg != raw  # actually rewritten


def test_bare_authenticate_failed_on_microsoft_host():
    msg = friendly_auth_error("smtp-mail.outlook.com", "AUTHENTICATE failed.")
    assert "Microsoft" in msg
    assert msg != "AUTHENTICATE failed."


def test_non_microsoft_host_passes_through_unchanged():
    raw = "AUTHENTICATE failed."
    assert friendly_auth_error("imap.gmail.com", raw) == raw


def test_microsoft_host_non_auth_error_passes_through():
    # A timeout / connection error on a Microsoft host is not an auth problem.
    raw = "timed out"
    assert friendly_auth_error("outlook.office365.com", raw) == raw


def test_empty_error_is_safe():
    assert friendly_auth_error("outlook.office365.com", "") == ""
