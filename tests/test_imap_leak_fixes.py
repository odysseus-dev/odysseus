"""Regression tests for IMAP connection leak fixes.

Each test forces an exception after _imap_connect() succeeds and asserts
that conn.logout() is still called exactly once (guaranteed by try/finally).

Functions covered:
  - routes/email_helpers.py: _fetch_sender_thread_context, _pre_retrieve_context
  - mcp_servers/email_server.py: _list_emails, _read_email, _reply_to_email,
    _download_attachment
"""

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
