import os
import tempfile
from pathlib import Path

import pytest

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus-email-imap-test-"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes.email_helpers import (
    _IMAP_TIMEOUT_SECONDS,
    _coerce_imap_timeout_seconds,
    _open_imap_connection,
)


class _FakeSock:
    def __init__(self):
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout


class _FakeIMAP:
    calls = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = _FakeSock()
        self.starttls_called = False
        _FakeIMAP.calls.append(("connect", self.__class__.__name__, host, port, timeout))

    def starttls(self):
        self.starttls_called = True
        _FakeIMAP.calls.append(("starttls", self.host, self.port))

    def login(self, user, password):
        _FakeIMAP.calls.append(("login", user, password))

    def logout(self):
        _FakeIMAP.calls.append(("logout", self.host, self.port))


class _FakeIMAPSSL(_FakeIMAP):
    pass


def test_imap_timeout_defaults_and_clamps():
    assert _coerce_imap_timeout_seconds(None) == 30
    assert _coerce_imap_timeout_seconds("nonsense") == 30
    assert _coerce_imap_timeout_seconds("2") == 5
    assert _coerce_imap_timeout_seconds("999") == 300


def test_open_imap_connection_uses_shared_timeout_for_implicit_ssl(monkeypatch):
    import routes.email_helpers as helpers

    _FakeIMAP.calls = []
    monkeypatch.setattr(helpers.imaplib, "IMAP4", _FakeIMAP)
    monkeypatch.setattr(helpers.imaplib, "IMAP4_SSL", _FakeIMAPSSL)

    conn = _open_imap_connection("imap.one.com", 993, starttls=False)

    assert _FakeIMAP.calls == [
        ("connect", "_FakeIMAPSSL", "imap.one.com", 993, _IMAP_TIMEOUT_SECONDS)
    ]
    assert conn.sock.timeout == _IMAP_TIMEOUT_SECONDS


def test_open_imap_connection_supports_starttls(monkeypatch):
    import routes.email_helpers as helpers

    _FakeIMAP.calls = []
    monkeypatch.setattr(helpers.imaplib, "IMAP4", _FakeIMAP)
    monkeypatch.setattr(helpers.imaplib, "IMAP4_SSL", _FakeIMAPSSL)

    _open_imap_connection("imap.local", 143, starttls=True)

    assert _FakeIMAP.calls == [
        ("connect", "_FakeIMAP", "imap.local", 143, _IMAP_TIMEOUT_SECONDS),
        ("starttls", "imap.local", 143),
    ]


@pytest.mark.asyncio
async def test_account_config_uses_shared_imap_timeout(monkeypatch):
    import routes.email_routes as email_routes

    captured = {}

    class _Conn:
        def login(self, user, password):
            captured["login"] = (user, password)

        def logout(self):
            captured["logout"] = True

    def fake_open(host, port, *, starttls, timeout):
        captured["open"] = (host, port, starttls, timeout)
        return _Conn()

    class _Req:
        async def json(self):
            return {
                "imap_host": "imap.one.com",
                "imap_port": 993,
                "imap_user": "user@example.com",
                "imap_password": "pw",
                "imap_starttls": False,
            }

    monkeypatch.setattr(email_routes, "_open_imap_connection", fake_open)

    router = email_routes.setup_email_routes()
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/email/accounts/test")

    result = await endpoint(_Req(), owner="")

    assert result["imap"] == {"ok": True}
    assert captured["open"] == ("imap.one.com", 993, False, _IMAP_TIMEOUT_SECONDS)
    assert captured["login"] == ("user@example.com", "pw")
    assert captured["logout"] is True
