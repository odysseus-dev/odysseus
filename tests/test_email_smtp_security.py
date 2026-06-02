import os
import tempfile
from pathlib import Path

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus-email-smtp-test-"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes.email_helpers import _send_smtp_message


class _FakeSMTP:
    calls = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        _FakeSMTP.calls.append(("connect", self.__class__.__name__, host, port))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.starttls_called = True
        _FakeSMTP.calls.append(("starttls", self.host, self.port))

    def login(self, user, password):
        _FakeSMTP.calls.append(("login", user, password))

    def sendmail(self, from_addr, recipients, message):
        _FakeSMTP.calls.append(("sendmail", from_addr, tuple(recipients), message, self.starttls_called))


class _FakeSMTPSSL(_FakeSMTP):
    pass


def _cfg(security, port=2525):
    return {
        "smtp_host": "smtp.local",
        "smtp_port": port,
        "smtp_security": security,
        "smtp_user": "user",
        "smtp_password": "pw",
    }


def test_send_smtp_message_supports_plain_smtp(monkeypatch):
    import routes.email_helpers as helpers

    _FakeSMTP.calls = []
    monkeypatch.setattr(helpers.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(helpers.smtplib, "SMTP_SSL", _FakeSMTPSSL)

    _send_smtp_message(_cfg("none"), "from@example.com", ["to@example.com"], "hello")

    assert _FakeSMTP.calls[0] == ("connect", "_FakeSMTP", "smtp.local", 2525)
    assert not any(call[0] == "starttls" for call in _FakeSMTP.calls)
    assert _FakeSMTP.calls[-1] == ("sendmail", "from@example.com", ("to@example.com",), "hello", False)


def test_send_smtp_message_supports_explicit_starttls(monkeypatch):
    import routes.email_helpers as helpers

    _FakeSMTP.calls = []
    monkeypatch.setattr(helpers.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(helpers.smtplib, "SMTP_SSL", _FakeSMTPSSL)

    _send_smtp_message(_cfg("starttls", port=2525), "from@example.com", ["to@example.com"], "hello")

    assert _FakeSMTP.calls[0] == ("connect", "_FakeSMTP", "smtp.local", 2525)
    assert ("starttls", "smtp.local", 2525) in _FakeSMTP.calls
    assert _FakeSMTP.calls[-1] == ("sendmail", "from@example.com", ("to@example.com",), "hello", True)


def test_send_smtp_message_defaults_587_to_starttls(monkeypatch):
    import routes.email_helpers as helpers

    _FakeSMTP.calls = []
    monkeypatch.setattr(helpers.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(helpers.smtplib, "SMTP_SSL", _FakeSMTPSSL)

    cfg = _cfg("", port=587)
    _send_smtp_message(cfg, "from@example.com", ["to@example.com"], "hello")

    assert _FakeSMTP.calls[0] == ("connect", "_FakeSMTP", "smtp.local", 587)
    assert ("starttls", "smtp.local", 587) in _FakeSMTP.calls


def test_send_smtp_message_uses_ssl_when_configured(monkeypatch):
    import routes.email_helpers as helpers

    _FakeSMTP.calls = []
    monkeypatch.setattr(helpers.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(helpers.smtplib, "SMTP_SSL", _FakeSMTPSSL)

    _send_smtp_message(_cfg("ssl", port=465), "from@example.com", ["to@example.com"], "hello")

    assert _FakeSMTP.calls[0] == ("connect", "_FakeSMTPSSL", "smtp.local", 465)
    assert not any(call[0] == "starttls" for call in _FakeSMTP.calls)
