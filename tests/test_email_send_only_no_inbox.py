"""A send-only (SMTP-only) account has no inbox to read.

`_imap_connect` must fail fast with a clear, typed error instead of handing an
empty host to imaplib — `imaplib.IMAP4("", 993)` silently dials localhost:993
and surfaces a confusing "[Errno 111] Connection refused" on every inbox poll.
"""
import os
import tempfile
from pathlib import Path

import pytest

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus-email-send-only-test-"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

import routes.email_helpers as helpers
from routes.email_helpers import EmailNotConfiguredError, _imap_connect


_SEND_ONLY_CFG = {
    "account_id": "acct-send-only",
    "account_name": "send-only",
    "smtp_host": "smtp.example.org",
    "smtp_port": 465,
    "smtp_user": "noreply@example.org",
    "smtp_password": "secret",
    "imap_host": "",          # <- the send-only marker
    "imap_port": 993,
    "imap_user": "",
    "imap_password": "",
    "imap_starttls": True,
    "from_address": "noreply@example.org",
}


def test_not_configured_error_is_runtime_error():
    # Subclassing RuntimeError keeps existing broad `except Exception` handlers
    # working while letting the inbox poll catch this case specifically.
    assert issubclass(EmailNotConfiguredError, RuntimeError)


def test_imap_connect_send_only_raises_and_never_dials(monkeypatch):
    monkeypatch.setattr(helpers, "_get_email_config", lambda *a, **k: dict(_SEND_ONLY_CFG))

    def _boom(*a, **k):  # opening a connection means we dialed an empty host
        raise AssertionError("send-only account must not open an IMAP connection")

    monkeypatch.setattr(helpers, "_open_imap_connection", _boom)

    with pytest.raises(EmailNotConfiguredError):
        _imap_connect("acct-send-only")


def test_imap_connect_with_host_still_connects(monkeypatch):
    # Guard must not regress normal accounts: a configured imap_host still
    # reaches _open_imap_connection.
    cfg = dict(_SEND_ONLY_CFG, imap_host="imap.example.org", imap_user="u", imap_password="p")
    monkeypatch.setattr(helpers, "_get_email_config", lambda *a, **k: cfg)

    opened = {}

    class _FakeConn:
        def login(self, user, password):
            opened["login"] = (user, password)

    def _fake_open(host, port, *, starttls, timeout):
        opened["host"] = host
        return _FakeConn()

    monkeypatch.setattr(helpers, "_open_imap_connection", _fake_open)

    conn = _imap_connect("acct-with-imap")
    assert opened["host"] == "imap.example.org"
    assert isinstance(conn, _FakeConn)
