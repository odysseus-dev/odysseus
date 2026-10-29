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

    def __init__(self, host, port, timeout=None, **_policy):
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
    monkeypatch.setattr(helpers, "_PolicyIMAP4", _FakeIMAP)
    monkeypatch.setattr(helpers, "_PolicyIMAP4_SSL", _FakeIMAPSSL)

    conn = _open_imap_connection("imap.one.com", 993, starttls=False)

    assert _FakeIMAP.calls == [
        ("connect", "_FakeIMAPSSL", "imap.one.com", 993, _IMAP_TIMEOUT_SECONDS)
    ]
    assert conn.sock.timeout == _IMAP_TIMEOUT_SECONDS


def test_open_imap_connection_supports_starttls(monkeypatch):
    import routes.email_helpers as helpers

    _FakeIMAP.calls = []
    monkeypatch.setattr(helpers, "_PolicyIMAP4", _FakeIMAP)
    monkeypatch.setattr(helpers, "_PolicyIMAP4_SSL", _FakeIMAPSSL)

    _open_imap_connection("imap.local", 143, starttls=True)

    assert _FakeIMAP.calls == [
        ("connect", "_FakeIMAP", "imap.local", 143, _IMAP_TIMEOUT_SECONDS),
        ("starttls", "imap.local", 143),
    ]



def test_open_imap_connection_forces_implicit_tls_on_993(monkeypatch):
    """Port 993 is IMAPS; a stale STARTTLS flag must not downgrade the dial.

    Older defaults paired imap_port=993 with imap_starttls=True, and honouring
    that flag opens a plaintext socket against a TLS-only listener: both ends
    block, and the server logs "no auth attempts in N secs ... TLS handshaking"
    when the client's socket timeout finally fires.
    """
    import routes.email_helpers as helpers

    _FakeIMAP.calls = []
    monkeypatch.setattr(helpers, "_PolicyIMAP4", _FakeIMAP)
    monkeypatch.setattr(helpers, "_PolicyIMAP4_SSL", _FakeIMAPSSL)

    conn = _open_imap_connection("imap.example.com", 993, starttls=True)

    assert _FakeIMAP.calls == [
        ("connect", "_FakeIMAPSSL", "imap.example.com", 993, _IMAP_TIMEOUT_SECONDS)
    ]
    assert not conn.starttls_called


def test_default_imap_starttls_follows_port():
    from src.constants import default_imap_starttls

    assert default_imap_starttls(993) is False
    assert default_imap_starttls("993") is False
    assert default_imap_starttls(None) is False
    assert default_imap_starttls(143) is True
    assert default_imap_starttls(31143) is True
    assert default_imap_starttls("bogus") is True


def test_google_oauth_transport_accepts_993_with_stale_starttls():
    from routes.email.email_routes import _google_oauth_imap_transport_allowed

    assert _google_oauth_imap_transport_allowed(993, True)
    assert _google_oauth_imap_transport_allowed(993, False)
    assert _google_oauth_imap_transport_allowed(143, True)
    assert not _google_oauth_imap_transport_allowed(143, False)


def test_mcp_load_config_forces_implicit_tls_on_993(monkeypatch):
    import mcp_servers.email_server as email_server

    monkeypatch.setenv("IMAP_PORT", "993")
    monkeypatch.delenv("IMAP_STARTTLS", raising=False)
    monkeypatch.setattr(email_server, "_ACCOUNT_CACHE", {})
    monkeypatch.setattr(email_server, "APP_DB", str(_tmp_data / "missing.db"))

    cfg = email_server._load_config()

    assert cfg["imap_ssl"] is True
    assert cfg["imap_starttls"] is False

@pytest.mark.asyncio
async def test_account_config_uses_shared_imap_timeout(monkeypatch):
    import routes.email_routes as email_routes

    captured = {}

    class _Conn:
        def login(self, user, password):
            captured["login"] = (user, password)

        def logout(self):
            captured["logout"] = True

    def fake_open(host, port, *, starttls, timeout, **_policy):
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
