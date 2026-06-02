"""Pin the null-owner-bypass fix in `routes/document_routes.py`.

The legacy `if session.owner and session.owner != user` short-circuit lets
any authenticated user read or write a session whose `owner IS NULL` (a
real shape — the codebase has `scripts/claim_ownerless.py` specifically
to backfill them, and #1288 just merged to fix a no-op in that script).
The maintainer closed the same gap in `routes/calendar_routes.py`
(`_get_or_404_calendar`), `note_routes.py`, `skills_routes.py`, and
`memory_routes.py` — but missed this one file. This test exercises the
new `_get_session_or_404` helper directly (mirroring
`tests/test_null_owner_gates.py:test_calendar_gate_*`), so the bug can't
regress at any of the three sites that now call it.

Pattern under test (multi-tenant deploy):
    user "alice" must NOT be able to read/write a session whose owner is
    None or whose owner is "bob", but single-user mode (`user=None`)
    must still see legacy null-owner sessions so AUTH_ENABLED=false /
    localhost installs keep working.
"""

import sys
import types
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock


for _stub in [
    "core.database",
    "core.auth",
    "src.endpoint_resolver",
]:
    if _stub not in sys.modules:
        m = types.ModuleType(_stub)
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


def _import_document_routes():
    mod_name = "routes.document_routes"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    return __import__(mod_name, fromlist=["_get_session_or_404"])


def test_session_gate_rejects_null_owner_for_authenticated_user():
    mod = _import_document_routes()
    db = MagicMock()
    sess = SimpleNamespace(id="s1", owner=None)
    db.query.return_value.filter.return_value.first.return_value = sess
    with pytest.raises(HTTPException) as exc:
        mod._get_session_or_404(db, "s1", user="alice")
    assert exc.value.status_code == 404
    assert "Session not found" in exc.value.detail


def test_session_gate_rejects_cross_owner():
    mod = _import_document_routes()
    db = MagicMock()
    sess = SimpleNamespace(id="s1", owner="bob")
    db.query.return_value.filter.return_value.first.return_value = sess
    with pytest.raises(HTTPException) as exc:
        mod._get_session_or_404(db, "s1", user="alice")
    assert exc.value.status_code == 404
    assert "Session not found" in exc.value.detail


def test_session_gate_accepts_matching_owner():
    mod = _import_document_routes()
    db = MagicMock()
    sess = SimpleNamespace(id="s1", owner="alice")
    db.query.return_value.filter.return_value.first.return_value = sess
    out = mod._get_session_or_404(db, "s1", user="alice")
    assert out is sess


def test_session_gate_accepts_null_owner_for_anonymous_user():
    """Single-user mode (user=None) must still see legacy null-owner
    sessions so the app continues to work in AUTH_ENABLED=false /
    localhost installs (mirrors the `if owner and ...` short-circuit
    at the front of every ownership check in the codebase)."""
    mod = _import_document_routes()
    db = MagicMock()
    sess = SimpleNamespace(id="s1", owner=None)
    db.query.return_value.filter.return_value.first.return_value = sess
    out = mod._get_session_or_404(db, "s1", user=None)
    assert out is sess


def test_session_gate_rejects_missing_session():
    mod = _import_document_routes()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(HTTPException) as exc:
        mod._get_session_or_404(db, "missing", user="alice")
    assert exc.value.status_code == 404
    assert "Session not found" in exc.value.detail
