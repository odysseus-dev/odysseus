"""User-supplied IMAP/SMTP hosts must pass the outbound host guard.

Regression: POST /api/email/accounts/test opened raw ``imaplib`` / ``smtplib``
connections to whatever host:port the caller supplied, and the account save
routes persisted arbitrary hosts for the background pollers to contact. Both
are reachable by any authenticated user (``require_user`` / ``require_owner``,
no admin gate), so a non-admin could port-scan loopback, the LAN and the cloud
metadata range through the server and read the first 200 chars of the banner
back out of the error message. THREAT_MODEL.md lists email as admin-only.

Policy (see routes/email_routes.py:_check_mail_host): link-local (metadata),
multicast, reserved and unspecified targets are always rejected; loopback and
private ranges are rejected for non-admin owners or when
EMAIL_BLOCK_PRIVATE_IPS=true, and stay allowed for admins (local-first: a LAN
Dovecot is a normal setup). Names that do not resolve pass through, because
the connect that follows fails with the same DNS error.
"""

import unittest.mock as mock

import pytest

from src.url_safety import check_outbound_host


# ── helper policy table ──────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "169.254.169.254",
    "[::ffff:169.254.169.254]",
    "::ffff:169.254.169.254",
    "fe80::1",
    "0.0.0.0",
    "224.0.0.1",
])
def test_always_blocked_addresses(host):
    ok, reason = check_outbound_host(host)
    assert ok is False, (host, reason)
    ok, reason = check_outbound_host(host, block_private=False)
    assert ok is False


# "::1" is deliberately absent: CPython < 3.13 classifies ::/8 as reserved, so
# the shared _classify() rejects IPv6 loopback unconditionally (same as
# check_outbound_url). Not asserted either way here.
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "100.64.1.1", "localhost", "LOCALHOST.", "mail.localhost", "ip6-localhost"])
def test_private_and_loopback_gated_by_block_private(host):
    ok, _ = check_outbound_host(host, block_private=True)
    assert ok is False, host
    ok, _ = check_outbound_host(host, block_private=False)
    assert ok is True, host


@pytest.mark.parametrize("host", ["host:993", "http://mail.example.com", "user@mail.example.com", "mail.example.com/", "a b", "", "   "])
def test_non_bare_hosts_rejected(host):
    ok, _ = check_outbound_host(host, resolver=lambda h: ["93.184.216.34"])
    assert ok is False, host


def test_nonstring_rejected():
    assert check_outbound_host(None)[0] is False  # type: ignore[arg-type]
    assert check_outbound_host(123)[0] is False  # type: ignore[arg-type]


def test_resolved_name_is_classified():
    ok, reason = check_outbound_host("metadata.google.internal", resolver=lambda h: ["169.254.169.254"], unresolved="allow")
    assert ok is False and "link-local" in reason
    ok, _ = check_outbound_host("mail.corp", block_private=True, resolver=lambda h: ["10.1.2.3"], unresolved="allow")
    assert ok is False
    ok, _ = check_outbound_host("mail.corp", block_private=False, resolver=lambda h: ["10.1.2.3"])
    assert ok is True
    ok, _ = check_outbound_host("imap.example.org", block_private=True, resolver=lambda h: ["93.184.216.34"])
    assert ok is True


def test_unresolved_policy():
    def _nx(h):
        raise OSError("Name or service not known")
    assert check_outbound_host("nope.invalid", resolver=_nx)[0] is False
    assert check_outbound_host("nope.invalid", resolver=_nx, unresolved="allow")[0] is True
    assert check_outbound_host("nope.invalid", resolver=lambda h: [])[0] is False
    assert check_outbound_host("nope.invalid", resolver=lambda h: [], unresolved="allow")[0] is True


# ── route wiring ─────────────────────────────────────────────────────────────

def _route(router, path, method):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _make_orm_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine)
    return Factory


def _set_admin(monkeypatch, is_admin: bool):
    monkeypatch.setattr("src.tool_security.owner_is_admin_or_single_user", lambda owner: is_admin)
    monkeypatch.delenv("EMAIL_BLOCK_PRIVATE_IPS", raising=False)


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_test_connection_rejects_loopback_for_non_admin_before_any_socket(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, False)
    router = email_routes.setup_email_routes()
    test_conn = _route(router, "/api/email/accounts/test", "POST")

    body = {
        "imap_host": "127.0.0.1", "imap_port": 11434, "imap_user": "x", "imap_password": "x",
        "imap_starttls": False,
        "smtp_host": "169.254.169.254", "smtp_port": 80, "smtp_security": "none",
    }
    with mock.patch("routes.email_routes._open_imap_connection") as imap_open, \
         mock.patch("routes.email_routes.smtplib.SMTP") as smtp, \
         mock.patch("routes.email_routes.smtplib.SMTP_SSL") as smtp_ssl:
        result = await test_conn(req=_Req(body), owner="regular-user")

    assert result["ok"] is False
    assert "Rejected IMAP host" in result["imap"]["error"]
    assert "Rejected SMTP host" in result["smtp"]["error"]
    imap_open.assert_not_called()
    smtp.assert_not_called()
    smtp_ssl.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_metadata_ip_rejected_even_for_admin(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, True)
    router = email_routes.setup_email_routes()
    test_conn = _route(router, "/api/email/accounts/test", "POST")

    body = {"imap_host": "169.254.169.254", "imap_port": 80, "imap_user": "x", "imap_password": "x", "imap_starttls": True}
    with mock.patch("routes.email_routes._open_imap_connection") as imap_open:
        result = await test_conn(req=_Req(body), owner="admin")
    assert result["ok"] is False
    assert "link-local" in result["imap"]["error"]
    imap_open.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_admin_may_use_loopback(monkeypatch):
    """Local-first: an admin's Dovecot on localhost keeps working."""
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, True)
    router = email_routes.setup_email_routes()
    test_conn = _route(router, "/api/email/accounts/test", "POST")

    body = {"imap_host": "127.0.0.1", "imap_port": 31143, "imap_user": "x", "imap_password": "x", "imap_starttls": False}
    with mock.patch("routes.email_routes._open_imap_connection", return_value=mock.MagicMock()) as imap_open:
        result = await test_conn(req=_Req(body), owner="admin")
    assert result["imap"]["ok"] is True
    imap_open.assert_called_once()


@pytest.mark.asyncio
async def test_create_account_rejects_metadata_host_before_db(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, True)
    router = email_routes.setup_email_routes()
    create = _route(router, "/api/email/accounts", "POST")

    def _no_db():
        raise AssertionError("DB must not be touched for a rejected host")

    with mock.patch("core.database.SessionLocal", _no_db):
        result = await create(
            {"name": "Backup", "imap_host": "169.254.169.254", "imap_user": "u", "imap_password": "p"},
            owner="admin",
        )
    assert result["ok"] is False
    assert "Rejected IMAP host" in result["error"]


@pytest.mark.asyncio
async def test_create_account_rejects_private_host_for_non_admin(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, False)
    router = email_routes.setup_email_routes()
    create = _route(router, "/api/email/accounts", "POST")
    with mock.patch("core.database.SessionLocal", _make_orm_db()):
        result = await create(
            {"name": "Scan", "imap_host": "mail.example.test", "smtp_host": "10.0.0.5", "imap_user": "u", "imap_password": "p"},
            owner="regular-user",
        )
    assert result["ok"] is False
    assert "Rejected SMTP host" in result["error"]


@pytest.mark.asyncio
async def test_create_account_admin_private_host_allowed_unless_locked_down(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, True)
    router = email_routes.setup_email_routes()
    create = _route(router, "/api/email/accounts", "POST")
    payload = {"name": "LAN", "imap_host": "192.168.1.10", "imap_user": "u", "imap_password": "p"}

    with mock.patch("core.database.SessionLocal", _make_orm_db()):
        result = await create(dict(payload), owner="admin")
    assert result["ok"] is True

    monkeypatch.setenv("EMAIL_BLOCK_PRIVATE_IPS", "true")
    with mock.patch("core.database.SessionLocal", _make_orm_db()):
        result = await create(dict(payload), owner="admin")
    assert result["ok"] is False
    assert "Rejected IMAP host" in result["error"]


@pytest.mark.asyncio
async def test_update_account_rejects_bad_host_and_keeps_row(monkeypatch):
    import routes.email_routes as email_routes
    from core.database import EmailAccount
    _set_admin(monkeypatch, False)
    Factory = _make_orm_db()
    db = Factory()
    db.add(EmailAccount(id="acct-1", owner="regular-user", name="Mine", imap_host="imap.gmail.com", imap_port=993))
    db.commit()
    db.close()

    router = email_routes.setup_email_routes()
    update = _route(router, "/api/email/accounts/{account_id}", "PUT")
    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._assert_owns_account", lambda account_id, owner: None):
        result = await update("acct-1", {"imap_host": "127.0.0.1", "imap_port": 8000}, owner="regular-user")
    assert result["ok"] is False
    assert "Rejected IMAP host" in result["error"]

    db = Factory()
    try:
        assert db.get(EmailAccount, "acct-1").imap_host == "imap.gmail.com"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_update_config_rejects_bad_host(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, False)
    router = email_routes.setup_email_routes()
    update_cfg = _route(router, "/api/email/config", "PUT")

    def _no_db():
        raise AssertionError("DB must not be touched for a rejected host")

    with mock.patch("core.database.SessionLocal", _no_db), \
         mock.patch("routes.email_routes._load_settings", _no_db):
        result = await update_cfg({"smtp_host": "10.0.0.1", "smtp_port": 6379}, account_id=None, owner="regular-user")
    assert result["ok"] is False
    assert "Rejected SMTP host" in result["error"]
