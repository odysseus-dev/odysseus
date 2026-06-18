"""The /accounts/test "Test connection" endpoint opens its own SMTP socket but
only wraps smtp.login() in a quit() finally — a rejected STARTTLS would leak the
open socket. Assert the connect-time guard closes it (same class as #3174/#3363).

Pulls the live test_account_config endpoint out of setup_email_routes() and calls
it directly, so it pins the real route's behaviour rather than a re-implementation.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-accts-smtp-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _test_account_endpoint():
    from routes.email_routes import setup_email_routes
    router = setup_email_routes()
    for route in router.routes:
        ep = getattr(route, "endpoint", None)
        if ep is not None and getattr(ep, "__name__", "") == "test_account_config":
            return ep
    raise AssertionError("test_account_config route not found")


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def test_accounts_test_closes_smtp_on_starttls_failure(monkeypatch):
    import routes.email_routes as er

    captured = {}
    smtp = MagicMock()
    smtp.close = MagicMock(side_effect=lambda: captured.__setitem__(
        "close", captured.get("close", 0) + 1))
    smtp.starttls = MagicMock(side_effect=Exception("STARTTLS rejected"))
    monkeypatch.setattr(er.smtplib, "SMTP", lambda *a, **kw: smtp)

    endpoint = _test_account_endpoint()
    body = {
        # no imap_* creds → the IMAP half short-circuits without connecting
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": "u@example.com",
        "smtp_password": "x",
    }
    resp = asyncio.run(endpoint(_Req(body), owner="alice"))

    assert captured.get("close", 0) == 1, (
        f"smtp.close() must be called exactly once when STARTTLS fails. "
        f"Got {captured.get('close')}")
    assert resp["smtp"]["ok"] is False
