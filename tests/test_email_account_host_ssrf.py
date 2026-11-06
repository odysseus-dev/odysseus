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
Dovecot is a normal setup). Unresolved names are rejected before persistence;
live connections also validate and pin their own resolved addresses.
"""

import socket
import unittest.mock as mock

import pytest

from src.url_safety import check_outbound_host, connect_outbound_tcp, OutboundAddressBlocked


# ── helper policy table ──────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "169.254.169.254",
    "[::ffff:169.254.169.254]",
    "::ffff:169.254.169.254",
    "fe80::1",
    "0.0.0.0",
    "224.0.0.1",
    "100.100.100.200",
    "[100.100.100.200]",
])
def test_always_blocked_addresses(host):
    ok, reason = check_outbound_host(host)
    assert ok is False, (host, reason)
    ok, reason = check_outbound_host(host, block_private=False)
    assert ok is False


@pytest.mark.parametrize("host", [
    "127.0.0.1",
    "::1",
    "[::1]",
    "10.0.0.5",
    "192.168.1.10",
    "100.64.1.1",
    "localhost",
    "LOCALHOST.",
    "mail.localhost",
    "ip6-localhost",
])
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
    ok, reason = check_outbound_host("metadata.google.internal", resolver=lambda h: ["169.254.169.254"])
    assert ok is False and "link-local" in reason
    ok, _ = check_outbound_host("mail.corp", block_private=True, resolver=lambda h: ["10.1.2.3"])
    assert ok is False
    ok, _ = check_outbound_host("mail.corp", block_private=False, resolver=lambda h: ["10.1.2.3"])
    assert ok is True
    ok, _ = check_outbound_host("imap.example.org", block_private=True, resolver=lambda h: ["93.184.216.34"])
    assert ok is True


def test_unresolved_policy():
    def _nx(h):
        raise OSError("Name or service not known")
    assert check_outbound_host("nope.invalid", resolver=_nx)[0] is False
    assert check_outbound_host("nope.invalid", resolver=lambda h: [])[0] is False


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
    monkeypatch.delenv("EMAIL_ALLOW_PRIVATE_IPS", raising=False)


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _run_connection_test_inline(monkeypatch, email_routes):
    # The tests below swap out socket.socket while awaiting the route. If the
    # route hops to a worker thread, the loop has to be woken through its
    # self-pipe, and on Windows the proactor checks that pipe with
    # isinstance(conn, socket.socket) -- which the mock breaks, so the loop
    # never wakes. Running the blocking helper inline keeps the loop idle
    # while the patch is active.
    async def _inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(email_routes, "run_in_threadpool", _inline)


@pytest.mark.asyncio
async def test_test_connection_rejects_loopback_for_non_admin_before_any_socket(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, False)
    _run_connection_test_inline(monkeypatch, email_routes)
    router = email_routes.setup_email_routes()
    test_conn = _route(router, "/api/email/accounts/test", "POST")

    body = {
        "imap_host": "127.0.0.1", "imap_port": 11434, "imap_user": "x", "imap_password": "x",
        "imap_starttls": False,
        "smtp_host": "169.254.169.254", "smtp_port": 80, "smtp_security": "none",
    }
    with mock.patch("socket.socket") as create_socket:
        result = await test_conn(req=_Req(body), owner="regular-user")

    assert result["ok"] is False
    assert ("Rejected IMAP host" in result["imap"]["error"] or "server address is not allowed" in result["imap"]["error"])
    assert ("Rejected SMTP host" in result["smtp"]["error"] or "server address is not allowed" in result["smtp"]["error"])
    create_socket.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_metadata_ip_rejected_even_for_admin(monkeypatch):
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, True)
    _run_connection_test_inline(monkeypatch, email_routes)
    router = email_routes.setup_email_routes()
    test_conn = _route(router, "/api/email/accounts/test", "POST")

    body = {"imap_host": "169.254.169.254", "imap_port": 80, "imap_user": "x", "imap_password": "x", "imap_starttls": True}
    with mock.patch("socket.socket") as create_socket:
        result = await test_conn(req=_Req(body), owner="admin")
    assert result["ok"] is False
    assert ("link-local" in result["imap"]["error"] or "server address is not allowed" in result["imap"]["error"])
    create_socket.assert_not_called()


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
            {"name": "Scan", "imap_host": "93.184.216.34", "smtp_host": "10.0.0.5", "imap_user": "u", "imap_password": "p"},
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


# ── adversarial runtime & connection-time tests ─────────────────────────────

def test_connect_outbound_tcp_blocks_dns_rebinding():
    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 993))]):
        with pytest.raises(OutboundAddressBlocked) as exc_info:
            connect_outbound_tcp("rebind.example.org", 993, block_private=False)
        assert "link-local" in str(exc_info.value)


def test_connect_outbound_tcp_multiple_ips_fails_closed():
    mixed_addrs = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 993)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 993)),
    ]
    with mock.patch("socket.getaddrinfo", return_value=mixed_addrs):
        with pytest.raises(OutboundAddressBlocked) as exc_info:
            connect_outbound_tcp("dual.example.org", 993, block_private=True)
        assert "private/shared/loopback" in str(exc_info.value)


@pytest.mark.asyncio
async def test_test_connection_endpoint_dns_rebinding_blocked(monkeypatch):
    """If DNS changes between save-check and connection, connect-time validation stops it."""
    import routes.email_routes as email_routes
    _set_admin(monkeypatch, False)
    _run_connection_test_inline(monkeypatch, email_routes)
    router = email_routes.setup_email_routes()
    test_conn = _route(router, "/api/email/accounts/test", "POST")

    body = {
        "imap_host": "rebind.example.org", "imap_port": 993, "imap_user": "u", "imap_password": "p",
        "imap_starttls": False,
    }
    rebinding_addrs = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 993))]
    with mock.patch("socket.getaddrinfo", return_value=rebinding_addrs), \
         mock.patch("socket.socket") as create_socket:
        result = await test_conn(req=_Req(body), owner="regular-user")

    assert result["ok"] is False
    assert ("Rejected IMAP host" in result["imap"]["error"] or "server address is not allowed" in result["imap"]["error"])
    create_socket.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_imap_connect_blocks_preexisting_db_row(monkeypatch):
    """A database row with private host (preexisting, restored, or DB-injected) fails closed at runtime."""
    import routes.email_helpers as email_helpers
    from core.database import EmailAccount
    _set_admin(monkeypatch, False)
    Factory = _make_orm_db()
    db = Factory()
    db.add(EmailAccount(id="acct-malicious", owner="regular-user", name="Evil", imap_host="127.0.0.1", imap_port=993, imap_user="u", imap_password="p"))
    db.commit()
    db.close()

    with mock.patch("core.database.SessionLocal", Factory):
        with pytest.raises(OutboundAddressBlocked) as exc_info:
            email_helpers._imap_connect("acct-malicious", owner="regular-user")
        assert "private/shared/loopback" in str(exc_info.value)


def test_runtime_send_smtp_message_blocks_metadata_host(monkeypatch):
    """_send_smtp_message fails closed on cloud metadata destination."""
    import routes.email_helpers as email_helpers
    _set_admin(monkeypatch, True)
    cfg = {
        "smtp_host": "169.254.169.254",
        "smtp_port": 25,
        "owner": "admin",
    }
    with pytest.raises(OutboundAddressBlocked) as exc_info:
        email_helpers._send_smtp_message(cfg, "from@example.com", ["to@example.com"], b"data")
    assert "link-local" in str(exc_info.value)


def test_ipv6_loopback_allowed_for_admin_blocked_for_non_admin():
    mock_sock = mock.MagicMock()
    with mock.patch("socket.socket", return_value=mock_sock):
        sock = connect_outbound_tcp("::1", 993, block_private=False)
        assert sock is mock_sock
        mock_sock.connect.assert_called_with(("::1", 993, 0, 0))

    with pytest.raises(OutboundAddressBlocked) as exc_info:
        connect_outbound_tcp("::1", 993, block_private=True)
    assert "private/shared/loopback" in str(exc_info.value)


def test_email_block_private_ips_env_overrides_admin(monkeypatch):
    import routes.email_helpers as email_helpers
    _set_admin(monkeypatch, True)
    monkeypatch.setenv("EMAIL_BLOCK_PRIVATE_IPS", "true")
    cfg = {
        "smtp_host": "127.0.0.1",
        "smtp_port": 25,
        "owner": "admin",
    }
    with pytest.raises(OutboundAddressBlocked) as exc_info:
        email_helpers._send_smtp_message(cfg, "from@example.com", ["to@example.com"], b"data")
    assert "private/shared/loopback" in str(exc_info.value)


def test_tls_sni_preserved_during_safe_connection():
    import routes.email_helpers as email_helpers
    mock_sock = mock.MagicMock()
    mock_ssl_sock = mock.MagicMock()
    mock_ctx = mock.MagicMock()
    mock_ctx.wrap_socket.return_value = mock_ssl_sock

    with mock.patch("routes.email_helpers.connect_outbound_tcp", return_value=mock_sock):
        imap = email_helpers._PolicyIMAP4_SSL.__new__(email_helpers._PolicyIMAP4_SSL)
        imap._block_private = False
        imap.host = "imap.secure.example.com"
        imap.port = 993
        imap.ssl_context = mock_ctx
        res = imap._create_socket(10)
        mock_ctx.wrap_socket.assert_called_with(mock_sock, server_hostname="imap.secure.example.com")
        assert res is mock_ssl_sock

    mock_smtp_sock = mock.MagicMock()
    mock_ctx.wrap_socket.return_value = mock_smtp_sock
    with mock.patch("routes.email_helpers.connect_outbound_tcp", return_value=mock_sock):
        smtp = email_helpers._PolicySMTP_SSL.__new__(email_helpers._PolicySMTP_SSL)
        smtp._block_private = False
        smtp._host = "smtp.secure.example.com"
        smtp.context = mock_ctx
        smtp.source_address = None
        res = smtp._get_socket("smtp.secure.example.com", 465, 10)
        mock_ctx.wrap_socket.assert_called_with(mock_sock, server_hostname="smtp.secure.example.com")
        assert res is mock_smtp_sock


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name", ["create", "update", "config"])
@pytest.mark.parametrize("field", ["imap_host", "smtp_host"])
@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize("answer", ["error", "empty", "garbage", "none"])
async def test_save_rejects_unresolved_hosts_before_persistence(monkeypatch, route_name, field, is_admin, answer):
    import routes.email_routes as routes
    import src.url_safety as safety

    _set_admin(monkeypatch, is_admin)
    def resolve(host):
        if answer == "error":
            raise RuntimeError("resolver-internal-detail")
        return {"empty": [], "garbage": ["not-an-ip"], "none": None}[answer]

    def no_persistence(*args, **kwargs):
        raise AssertionError("Rejected mail host reached persistence")

    monkeypatch.setattr(safety, "_default_resolver", resolve)
    monkeypatch.setattr("core.database.SessionLocal", no_persistence)
    monkeypatch.setattr(routes, "_load_settings", no_persistence)
    monkeypatch.setattr(routes, "_save_settings", no_persistence)
    monkeypatch.setattr(routes, "_assert_owns_account", lambda *a: None)
    router = routes.setup_email_routes()
    payload = {"name": "Pending DNS", field: "pending.example.invalid"}
    owner = "admin" if is_admin else "regular-user"
    if route_name == "create":
        result = await _route(router, "/api/email/accounts", "POST")(payload, owner=owner)
    elif route_name == "update":
        result = await _route(router, "/api/email/accounts/{account_id}", "PUT")("acct", payload, owner=owner)
    else:
        result = await _route(router, "/api/email/config", "PUT")(payload, account_id=None, owner=owner)
    assert result["ok"] is False
    assert "does not resolve" in result["error"]
    assert "resolver-internal-detail" not in result["error"]
    assert "not-an-ip" not in result["error"]


def test_connect_pins_the_first_resolution_without_rebinding_lookup():
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 993))]
    metadata = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 993))]
    resolver = mock.Mock(side_effect=[public, metadata])
    with mock.patch("socket.socket") as create_socket:
        sock = connect_outbound_tcp("rebind.example.org", 993, resolver=resolver, block_private=True)
    resolver.assert_called_once_with("rebind.example.org", 993, 0, socket.SOCK_STREAM)
    sock.connect.assert_called_once_with(("93.184.216.34", 993))


def test_resolver_type_error_propagates_without_alternate_signature_retry():
    resolver = mock.Mock(side_effect=TypeError("resolver-internal-bug"))
    with mock.patch("socket.socket") as create_socket:
        with pytest.raises(TypeError, match="resolver-internal-bug"):
            connect_outbound_tcp("mail.example.org", 993, resolver=resolver)
    resolver.assert_called_once_with("mail.example.org", 993, 0, socket.SOCK_STREAM)
    create_socket.assert_not_called()


@pytest.mark.parametrize("port", [None, 0, -1, 65536, True, 25.5, "not-a-port"])
def test_invalid_tcp_port_is_rejected_before_resolution(port):
    resolver = mock.Mock()
    with pytest.raises(ValueError):
        connect_outbound_tcp("mail.example.org", port, resolver=resolver)
    resolver.assert_not_called()


def test_empty_resolver_iterator_fails_closed():
    assert check_outbound_host("empty.example.org", resolver=lambda host: iter(()))[0] is False


def test_shared_space_metadata_endpoint_is_identified_correctly():
    ok, reason = check_outbound_host("100.100.100.200", block_private=False)
    assert ok is False
    assert "metadata endpoint blocked" in reason
    assert "link-local" not in reason


def test_importing_mail_helpers_does_not_replace_stdlib_clients():
    import subprocess
    import sys

    subprocess.run([sys.executable, "-c", """
import imaplib, smtplib
original = (imaplib.IMAP4, imaplib.IMAP4_SSL, smtplib.SMTP, smtplib.SMTP_SSL)
import routes.email.email_helpers
assert original == (imaplib.IMAP4, imaplib.IMAP4_SSL, smtplib.SMTP, smtplib.SMTP_SSL)
"""], check=True, capture_output=True, text=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name", ["create", "update", "config"])
async def test_save_host_resolution_runs_off_the_event_loop(monkeypatch, route_name):
    import threading
    import routes.email_routes as routes

    event_loop_thread = threading.get_ident()
    def guard(*args):
        assert threading.get_ident() != event_loop_thread
        return "Host rejected"

    monkeypatch.setattr(routes, "_check_mail_host", guard)
    monkeypatch.setattr(routes, "_assert_owns_account", lambda *args: None)
    router = routes.setup_email_routes()
    payload = {"name": "Pending DNS", "imap_host": "pending.example.invalid"}
    if route_name == "create":
        result = await _route(router, "/api/email/accounts", "POST")(payload, owner="admin")
    elif route_name == "update":
        result = await _route(router, "/api/email/accounts/{account_id}", "PUT")("acct", payload, owner="admin")
    else:
        result = await _route(router, "/api/email/config", "PUT")(payload, account_id=None, owner="admin")
    assert result == {"ok": False, "error": "Host rejected"}


