"""Regression tests for IMAP connection leak fixes.

Each test forces an exception after _imap_connect() succeeds and asserts
that conn.logout() is still called exactly once (guaranteed by try/finally).

Functions covered:
  - routes/email_helpers.py: _fetch_sender_thread_context, _pre_retrieve_context
  - mcp_servers/email_server.py: _list_emails, _read_email, _reply_to_email,
    _download_attachment
"""

import imaplib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-imap-leak-fixes-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_failing_conn(captured, *, raises_on="select"):
    """Return a mock IMAP connection that raises on the first call to `raises_on`."""
    conn = MagicMock()
    conn.logout = MagicMock(side_effect=lambda: captured.__setitem__(
        "logout_calls", captured.get("logout_calls", 0) + 1
    ))

    def _raise(*a, **kw):
        raise RuntimeError("simulated IMAP failure")

    getattr(conn, raises_on).side_effect = _raise
    return conn


# ── email_helpers ──────────────────────────────────────────────────────────────

def test_fetch_sender_thread_context_logs_out_on_select_failure(monkeypatch):
    import routes.email_helpers as helpers

    captured = {}
    conn = _make_failing_conn(captured, raises_on="select")
    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: conn)

    result = helpers._fetch_sender_thread_context("user@example.com")

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called on select failure. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )
    assert result == "", "Should return empty string on failure"


def test_fetch_sender_thread_context_logs_out_on_connect_failure(monkeypatch):
    """If _imap_connect itself raises, conn is None — no logout, no crash."""
    import routes.email_helpers as helpers

    def _fail(*a, **kw):
        raise ConnectionRefusedError("cannot connect")

    monkeypatch.setattr(helpers, "_imap_connect", _fail)
    result = helpers._fetch_sender_thread_context("user@example.com")
    assert result == "", "Should return empty string when connect fails"


def test_pre_retrieve_context_logs_out_on_search_failure(monkeypatch):
    import routes.email_helpers as helpers

    captured = {}
    conn = MagicMock()
    conn.select.return_value = ("OK", [])
    conn.logout = MagicMock(side_effect=lambda: captured.__setitem__(
        "logout_calls", captured.get("logout_calls", 0) + 1
    ))
    conn.search.side_effect = RuntimeError("simulated search failure")

    monkeypatch.setattr(helpers, "_imap_connect", lambda *a, **kw: conn)

    # Bypass the known-sender check and term extraction so we reach the IMAP block
    monkeypatch.setattr(helpers, "_imap", MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=("OK", [])),
                search=MagicMock(return_value=("OK", [b"1"])),
            )),
            __exit__=MagicMock(return_value=False),
        )
    ))

    # Provide a body with a capitalised term so terms_list is non-empty
    snippets, terms = helpers._pre_retrieve_context(
        body="Project Alpha update",
        sender="Known Sender <known@example.com>",
    )

    # The function is best-effort and never raises; logout must have been called
    assert captured.get("logout_calls", 0) == 1, (
        f"ctx_conn.logout() must be called even when search raises. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


# ── email_server ───────────────────────────────────────────────────────────────

def test_mcp_list_emails_logs_out_on_select_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = _make_failing_conn(captured, raises_on="select")
    monkeypatch.setattr(srv, "_imap_connect", lambda *a, **kw: conn)

    try:
        srv._list_emails()
    except Exception:
        pass

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called after select raises. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


def test_mcp_list_emails_logs_out_on_search_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = MagicMock()
    conn.select.return_value = ("OK", [])
    conn.uid.side_effect = RuntimeError("simulated search failure")
    conn.logout = MagicMock(side_effect=lambda: captured.__setitem__(
        "logout_calls", captured.get("logout_calls", 0) + 1
    ))
    monkeypatch.setattr(srv, "_imap_connect", lambda *a, **kw: conn)

    try:
        srv._list_emails()
    except Exception:
        pass

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called after uid search raises. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


def test_mcp_read_email_logs_out_on_select_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = _make_failing_conn(captured, raises_on="select")
    monkeypatch.setattr(srv, "_imap_connect", lambda *a, **kw: conn)
    monkeypatch.setattr(srv, "_load_config", lambda *a, **kw: {})

    # The exception propagates out of _read_email (no outer catch in this fn);
    # what matters is that logout was still called via finally before it did.
    try:
        srv._read_email(uid="1")
    except RuntimeError:
        pass

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called after select raises. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


def test_mcp_read_email_logs_out_on_fetch_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = MagicMock()
    conn.select.return_value = ("OK", [])
    conn.uid.side_effect = RuntimeError("simulated fetch failure")
    conn.logout = MagicMock(side_effect=lambda: captured.__setitem__(
        "logout_calls", captured.get("logout_calls", 0) + 1
    ))
    monkeypatch.setattr(srv, "_imap_connect", lambda *a, **kw: conn)
    monkeypatch.setattr(srv, "_load_config", lambda *a, **kw: {})

    try:
        srv._read_email(uid="1")
    except RuntimeError:
        pass

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called after uid fetch raises. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


def test_mcp_reply_to_email_logs_out_on_select_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = _make_failing_conn(captured, raises_on="select")
    monkeypatch.setattr(srv, "_imap_connect", lambda *a, **kw: conn)

    # Exception propagates; the finally still runs before it does.
    try:
        srv._reply_to_email(uid="1", body="hi")
    except RuntimeError:
        pass

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called after select raises in _reply_to_email. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


def test_mcp_download_attachment_logs_out_on_select_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = _make_failing_conn(captured, raises_on="select")
    monkeypatch.setattr(srv, "_imap_connect", lambda *a, **kw: conn)

    try:
        srv._download_attachment(uid="1", index=0)
    except RuntimeError:
        pass

    assert captured.get("logout_calls", 0) == 1, (
        f"conn.logout() must be called after select raises in _download_attachment. "
        f"Got logout_calls={captured.get('logout_calls')}"
    )


# ── connect-time leak: _imap_connect / _open_imap_connection (#3174) ──────────
# The cases above all monkeypatch _imap_connect to *succeed*; these cover the
# gap where the connect itself fails (bad/expired app password, rejected
# STARTTLS) and the already-open socket would otherwise be orphaned.


def test_imap_connect_shuts_down_socket_on_login_failure(monkeypatch):
    """A failed login() must close the already-connected socket, not leak it."""
    import routes.email_helpers as helpers

    captured = {}
    conn = MagicMock()
    conn.shutdown = MagicMock(side_effect=lambda: captured.__setitem__(
        "shutdown_calls", captured.get("shutdown_calls", 0) + 1
    ))
    conn.login = MagicMock(side_effect=imaplib.IMAP4.error(b"AUTHENTICATE failed."))

    monkeypatch.setattr(helpers, "_get_email_config", lambda *a, **kw: {
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_starttls": False,
        "imap_user": "user@example.com",
        "imap_password": "wrong",
    })
    monkeypatch.setattr(helpers, "_open_imap_connection", lambda *a, **kw: conn)

    raised = False
    try:
        helpers._imap_connect()
    except Exception:
        raised = True

    assert raised, "login failure must propagate to the caller"
    assert captured.get("shutdown_calls", 0) == 1, (
        f"conn.shutdown() must be called exactly once when login fails. "
        f"Got shutdown_calls={captured.get('shutdown_calls')}"
    )


def test_open_imap_connection_shuts_down_on_starttls_failure(monkeypatch):
    """A rejected STARTTLS upgrade must close the open plain socket."""
    import routes.email_helpers as helpers

    captured = {}
    conn = MagicMock()
    conn.shutdown = MagicMock(side_effect=lambda: captured.__setitem__(
        "shutdown_calls", captured.get("shutdown_calls", 0) + 1
    ))
    conn.starttls = MagicMock(side_effect=RuntimeError("STARTTLS rejected"))

    monkeypatch.setattr(helpers.imaplib, "IMAP4", lambda *a, **kw: conn)

    raised = False
    try:
        helpers._open_imap_connection("imap.example.com", 143, starttls=True)
    except Exception:
        raised = True

    assert raised, "starttls failure must propagate to the caller"
    assert captured.get("shutdown_calls", 0) == 1, (
        f"conn.shutdown() must be called exactly once when STARTTLS fails. "
        f"Got shutdown_calls={captured.get('shutdown_calls')}"
    )


# ── connect-time leak: mcp_servers/email_server.py (folded in per review #3363) ──
# Same connect-then-step pattern as the routes path. IMAP closes pre-auth with
# shutdown(); SMTP has no shutdown(), so close() (socket close, no QUIT).


def _cfg_imap(ssl=True, starttls=False):
    return {
        "imap_ssl": ssl, "imap_starttls": starttls,
        "imap_host": "imap.example.com", "imap_port": 993,
        "imap_user": "user@example.com", "imap_password": "wrong",
    }


def test_mcp_imap_connect_shuts_down_on_login_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = MagicMock()
    conn.shutdown = MagicMock(side_effect=lambda: captured.__setitem__(
        "shutdown_calls", captured.get("shutdown_calls", 0) + 1))
    conn.login = MagicMock(side_effect=imaplib.IMAP4.error(b"AUTHENTICATE failed."))
    monkeypatch.setattr(srv, "_load_config", lambda *a, **kw: _cfg_imap(ssl=True))
    monkeypatch.setattr(srv.imaplib, "IMAP4_SSL", lambda *a, **kw: conn)

    raised = False
    try:
        srv._imap_connect()
    except Exception:
        raised = True
    assert raised, "login failure must propagate"
    assert captured.get("shutdown_calls", 0) == 1, (
        f"shutdown() must be called once on MCP IMAP login failure. Got {captured.get('shutdown_calls')}")


def test_mcp_imap_connect_shuts_down_on_starttls_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = MagicMock()
    conn.shutdown = MagicMock(side_effect=lambda: captured.__setitem__(
        "shutdown_calls", captured.get("shutdown_calls", 0) + 1))
    conn.starttls = MagicMock(side_effect=RuntimeError("STARTTLS rejected"))
    monkeypatch.setattr(srv, "_load_config", lambda *a, **kw: _cfg_imap(ssl=False, starttls=True))
    monkeypatch.setattr(srv.imaplib, "IMAP4", lambda *a, **kw: conn)

    raised = False
    try:
        srv._imap_connect()
    except Exception:
        raised = True
    assert raised, "starttls failure must propagate"
    assert captured.get("shutdown_calls", 0) == 1, (
        f"shutdown() must be called once on MCP IMAP STARTTLS failure. Got {captured.get('shutdown_calls')}")


def _cfg_smtp(security):
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587 if security == "starttls" else 465,
        "smtp_security": security, "smtp_user": "user@example.com",
        "smtp_password": "wrong", "account_name": "test",
    }


def test_mcp_smtp_connect_closes_on_login_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = MagicMock()
    conn.close = MagicMock(side_effect=lambda: captured.__setitem__(
        "close_calls", captured.get("close_calls", 0) + 1))
    conn.login = MagicMock(side_effect=Exception("SMTP auth failed"))
    monkeypatch.setattr(srv, "_load_config", lambda *a, **kw: _cfg_smtp("ssl"))
    monkeypatch.setattr(srv, "_smtp_ready", lambda cfg: True)
    monkeypatch.setattr(srv.smtplib, "SMTP_SSL", lambda *a, **kw: conn)

    raised = False
    try:
        srv._smtp_connect()
    except Exception:
        raised = True
    assert raised, "login failure must propagate"
    assert captured.get("close_calls", 0) == 1, (
        f"close() must be called once on MCP SMTP login failure. Got {captured.get('close_calls')}")


def test_mcp_smtp_connect_closes_on_starttls_failure(monkeypatch):
    import mcp_servers.email_server as srv

    captured = {}
    conn = MagicMock()
    conn.close = MagicMock(side_effect=lambda: captured.__setitem__(
        "close_calls", captured.get("close_calls", 0) + 1))
    conn.starttls = MagicMock(side_effect=Exception("STARTTLS rejected"))
    monkeypatch.setattr(srv, "_load_config", lambda *a, **kw: _cfg_smtp("starttls"))
    monkeypatch.setattr(srv, "_smtp_ready", lambda cfg: True)
    monkeypatch.setattr(srv.smtplib, "SMTP", lambda *a, **kw: conn)

    raised = False
    try:
        srv._smtp_connect()
    except Exception:
        raised = True
    assert raised, "starttls failure must propagate"
    assert captured.get("close_calls", 0) == 1, (
        f"close() must be called once on MCP SMTP STARTTLS failure. Got {captured.get('close_calls')}")
