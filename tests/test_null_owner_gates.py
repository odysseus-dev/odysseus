"""Pin the null-owner-bypass fixes so they don't regress.

The same legacy `if row.owner and row.owner != user` / `(owner == user) |
(owner == None)` pattern has regressed THREE times across reviews —
once in gallery, once in calendar, once in notes/daily-brief. Without
tests it'll keep coming back. These tests exercise the small helper
functions directly against MagicMock'd model rows.

Pattern under test (multi-tenant deploy):
  user "alice" must NOT be able to read/write a row whose owner is None
  or whose owner is "bob".
"""

import os
import sys
import types
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

# `tests/conftest.py` stubs the heavy optional deps. We additionally
# stub `core.database` here because the real module instantiates
# SQLAlchemy declarative classes at import-time — which blows up under
# the conftest's `sqlalchemy.*` MagicMock stubs ("metaclass conflict").
# Stub also a handful of route modules each of these targeted modules
# happens to drag in at import-time.
for _stub in [
    "core.database",
    "core.auth",
    "src.endpoint_resolver",
]:
    if _stub not in sys.modules:
        m = types.ModuleType(_stub)
        # Provide the names the importers will look up.
        if _stub == "core.database":
            m.Base = MagicMock()
            m.SessionLocal = MagicMock()
            m.CalendarCal = MagicMock()
            m.CalendarEvent = MagicMock()
            m.Document = MagicMock()
            m.DocumentVersion = MagicMock()
            m.Session = MagicMock()
            m.ChatMessage = MagicMock()
            m.GalleryImage = MagicMock()
            m.GalleryAlbum = MagicMock()
            m.Note = MagicMock()
            m.ScheduledTask = MagicMock()
            m.TaskRun = MagicMock()
            m.ModelEndpoint = MagicMock()
        elif _stub == "core.auth":
            m.AuthManager = MagicMock()
        sys.modules[_stub] = m

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# calendar._get_or_404_calendar / _get_or_404_event
# ---------------------------------------------------------------------------

def _import_calendar_helpers():
    """Import the two private gate helpers without booting the full
    calendar router. We patch sys.modules so the module-load side
    effects (DB import) don't blow up under the conftest stubs."""
    mod_name = "routes.calendar_routes"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    # core.database is stubbed by conftest already; the module should
    # import cleanly.
    return __import__(mod_name, fromlist=["_get_or_404_calendar", "_get_or_404_event"])


def test_calendar_gate_rejects_null_owner_for_authenticated_user():
    cal_mod = _import_calendar_helpers()
    db = MagicMock()
    cal = SimpleNamespace(id="c1", owner=None)
    db.query.return_value.filter.return_value.first.return_value = cal
    with pytest.raises(HTTPException) as exc:
        cal_mod._get_or_404_calendar(db, "c1", owner="alice")
    assert exc.value.status_code == 404


def test_calendar_gate_rejects_cross_owner():
    cal_mod = _import_calendar_helpers()
    db = MagicMock()
    cal = SimpleNamespace(id="c1", owner="bob")
    db.query.return_value.filter.return_value.first.return_value = cal
    with pytest.raises(HTTPException) as exc:
        cal_mod._get_or_404_calendar(db, "c1", owner="alice")
    assert exc.value.status_code == 404


def test_calendar_gate_accepts_matching_owner():
    cal_mod = _import_calendar_helpers()
    db = MagicMock()
    cal = SimpleNamespace(id="c1", owner="alice")
    db.query.return_value.filter.return_value.first.return_value = cal
    out = cal_mod._get_or_404_calendar(db, "c1", owner="alice")
    assert out is cal


def test_calendar_event_gate_rejects_null_owner_calendar():
    cal_mod = _import_calendar_helpers()
    db = MagicMock()
    cal = SimpleNamespace(owner=None)
    ev = SimpleNamespace(uid="e1", calendar=cal)
    db.query.return_value.join.return_value.filter.return_value.first.return_value = ev
    with pytest.raises(HTTPException) as exc:
        cal_mod._get_or_404_event(db, "e1", owner="alice")
    assert exc.value.status_code == 404


def test_calendar_event_gate_rejects_cross_owner():
    cal_mod = _import_calendar_helpers()
    db = MagicMock()
    cal = SimpleNamespace(owner="bob")
    ev = SimpleNamespace(uid="e1", calendar=cal)
    db.query.return_value.join.return_value.filter.return_value.first.return_value = ev
    with pytest.raises(HTTPException) as exc:
        cal_mod._get_or_404_event(db, "e1", owner="alice")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# document._owner_session_filter
# ---------------------------------------------------------------------------

def test_document_owner_filter_rejects_anonymous():
    from routes.document_routes import _owner_session_filter
    fake_q = MagicMock()
    out = _owner_session_filter(fake_q, user=None)
    # The fix should call .filter(False) — fake_q.filter was invoked once
    fake_q.filter.assert_called_once()
    # And the resulting query is whatever the chained mock returns.
    assert out is fake_q.filter.return_value


def test_document_owner_filter_applies_owner_clause():
    from routes.document_routes import _owner_session_filter
    fake_q = MagicMock()
    out = _owner_session_filter(fake_q, user="alice")
    fake_q.filter.assert_called_once()  # one strict filter call
    assert out is fake_q.filter.return_value


# ---------------------------------------------------------------------------
# gallery._owner_filter
# ---------------------------------------------------------------------------

def test_gallery_owner_filter_blocks_anonymous():
    from routes.gallery_routes import _owner_filter
    fake_q = MagicMock()
    out = _owner_filter(fake_q, user=None)
    # Anonymous → q.filter(False) → contradiction, empty result set.
    fake_q.filter.assert_called_once_with(False)
    assert out is fake_q.filter.return_value


def test_gallery_owner_filter_passes_user():
    from routes.gallery_routes import _owner_filter
    fake_q = MagicMock()
    out = _owner_filter(fake_q, user="alice")
    # Under the SQLAlchemy MagicMock stubs we can't introspect the
    # column clause; verifying that filter() was invoked exactly once
    # (and returned its mocked query) is enough to guard the signature
    # and stop a regression where the function silently no-ops on
    # logged-in users.
    fake_q.filter.assert_called_once()
    assert out is fake_q.filter.return_value
