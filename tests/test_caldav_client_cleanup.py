"""Issue #4593 — the CalDAV DAVClient must be closed on every path.

`_sync_blocking` (src/caldav_sync.py) and `_writeback_blocking`
(src/caldav_writeback.py) each open their own DAVClient. The client holds an
HTTP session with pooled connections; if it is never closed those connections
leak for the lifetime of the process. These tests pin that the client is
closed on the discovery early-returns, the normal return, and the
write-back paths, using a fake client so no network or `caldav` install is
needed.
"""

import sys
import types

import pytest
from unittest.mock import MagicMock


def _stub_sync_deps(monkeypatch):
    """Make `_sync_blocking`'s lazy imports resolve without a real caldav/db."""
    err_mod = types.ModuleType("caldav.lib.error")

    class AuthorizationError(Exception):
        pass

    class NotFoundError(Exception):
        pass

    err_mod.AuthorizationError = AuthorizationError
    err_mod.NotFoundError = NotFoundError
    monkeypatch.setitem(sys.modules, "caldav", types.ModuleType("caldav"))
    monkeypatch.setitem(sys.modules, "caldav.lib", types.ModuleType("caldav.lib"))
    monkeypatch.setitem(sys.modules, "caldav.lib.error", err_mod)

    db_mod = types.ModuleType("core.database")
    db_mod.CalendarCal = MagicMock()
    db_mod.CalendarEvent = MagicMock()
    db_mod.CalendarDeletedEvent = MagicMock()
    db_mod.SessionLocal = MagicMock()
    if "core" not in sys.modules:
        monkeypatch.setitem(sys.modules, "core", types.ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core.database", db_mod)

    # Stub routes.calendar_routes so the lazy import of _ensure_positive_duration
    # inside _sync_blocking doesn't drag in dateutil / FastAPI / SQLAlchemy.
    routes_mod = types.ModuleType("routes")
    cal_routes_mod = types.ModuleType("routes.calendar_routes")
    cal_routes_mod._ensure_positive_duration = lambda start, end, all_day: end
    if "routes" not in sys.modules:
        monkeypatch.setitem(sys.modules, "routes", routes_mod)
    monkeypatch.setitem(sys.modules, "routes.calendar_routes", cal_routes_mod)

    return AuthorizationError


def test_sync_closes_client_on_discovery_auth_failure(monkeypatch):
    import src.caldav_sync as sync

    AuthorizationError = _stub_sync_deps(monkeypatch)
    client = MagicMock()
    client.principal.side_effect = AuthorizationError("bad credentials")
    monkeypatch.setattr(sync, "_build_dav_client", lambda *a, **k: client)

    result = sync._sync_blocking("alice", "https://dav.example.com/", "u", "p")

    client.close.assert_called_once()
    assert any("Discovery failed" in e for e in result["errors"])


def test_sync_closes_client_when_url_fallback_fails(monkeypatch):
    import src.caldav_sync as sync

    _stub_sync_deps(monkeypatch)
    client = MagicMock()
    # principal() raises a generic error -> the URL-as-calendar fallback is
    # tried; make that fail too so the function hits the early return.
    client.principal.side_effect = RuntimeError("no principal endpoint")
    monkeypatch.setattr(sync, "_build_dav_client", lambda *a, **k: client)
    monkeypatch.setattr(
        sync, "_open_url_as_calendar",
        MagicMock(side_effect=RuntimeError("not a calendar")),
    )

    result = sync._sync_blocking("alice", "https://dav.example.com/", "u", "p")

    client.close.assert_called_once()
    assert result["errors"]


def test_writeback_closes_client_when_no_calendars(monkeypatch):
    import src.caldav_sync as sync
    import src.caldav_writeback as wb

    client = MagicMock()
    monkeypatch.setattr(sync, "_build_dav_client", lambda *a, **k: client)
    monkeypatch.setattr(wb, "_discover_calendars", lambda c: [])

    result = wb._writeback_blocking(
        "caldav-1", {"uid": "evt-1"}, False, "https://dav.example.com/", "u", "p"
    )

    client.close.assert_called_once()
    assert result["ok"] is False


def test_writeback_closes_client_on_success(monkeypatch):
    import src.caldav_sync as sync
    import src.caldav_writeback as wb

    client = MagicMock()
    monkeypatch.setattr(sync, "_build_dav_client", lambda *a, **k: client)
    monkeypatch.setattr(wb, "_discover_calendars", lambda c: [MagicMock()])
    monkeypatch.setattr(wb, "push_event", lambda *a, **k: {"ok": True})

    result = wb._writeback_blocking(
        "caldav-1", {"uid": "evt-1"}, False, "https://dav.example.com/", "u", "p"
    )

    client.close.assert_called_once()
    assert result["ok"] is True


def test_sync_closes_client_when_session_local_raises(monkeypatch):
    import src.caldav_sync as sync

    AuthorizationError = _stub_sync_deps(monkeypatch)

    # Give principal() a working response so discovery passes
    mock_principal = MagicMock()
    mock_cal = MagicMock()
    mock_cal.url = "https://dav.example.com/alice/home/"
    mock_principal.calendars.return_value = [mock_cal]

    client = MagicMock()
    client.principal.return_value = mock_principal
    monkeypatch.setattr(sync, "_build_dav_client", lambda *a, **k: client)

    # Make SessionLocal blow up before any DB work
    import sys
    sys.modules["core.database"].SessionLocal.side_effect = RuntimeError("DB unavailable")

    with pytest.raises(RuntimeError, match="DB unavailable"):
        sync._sync_blocking("alice", "https://dav.example.com/", "u", "p")

    client.close.assert_called_once()
