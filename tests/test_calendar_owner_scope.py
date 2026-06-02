"""Pin owner-scoping of the autonomous email->calendar event snapshot.

The email auto-calendar pass fans out over EVERY user's mailbox and used to
feed an *unscoped* upcoming-events snapshot to the extraction LLM, then execute
the model's create/update/delete ops via do_manage_calendar with owner=None —
so processing one tenant's mail could read AND mutate another tenant's calendar
(and leak every tenant's event titles to the LLM endpoint).

The fix routes the snapshot through core.database.get_upcoming_events(owner)
and passes the account owner to do_manage_calendar. This test pins that
get_upcoming_events scopes to the owner; it fails if the owner filter is
dropped (the original cross-tenant behavior).
"""
import ast
import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def test_get_upcoming_events_is_owner_scoped():
    source = Path("core/database.py").read_text()
    tree = ast.parse(source)
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_upcoming_events"
    )
    body = ast.unparse(fn)

    assert "join(CalendarCal)" in body
    assert "if owner is not None:" in body
    assert "q.filter(CalendarCal.owner == owner)" in body


class _Expr:
    def __init__(self, op, field=None, value=None, children=()):
        self.op = op
        self.field = field
        self.value = value
        self.children = tuple(children)

    def __or__(self, other):
        return _Expr("or", children=(self, other))

    def __and__(self, other):
        return _Expr("and", children=(self, other))


class _Column:
    def __init__(self, field):
        self.field = field

    def __eq__(self, value):
        return _Expr("eq", self.field, value)

    def __ne__(self, value):
        return _Expr("ne", self.field, value)

    def __lt__(self, value):
        return _Expr("lt", self.field, value)

    def __gt__(self, value):
        return _Expr("gt", self.field, value)

    def is_(self, value):
        return _Expr("is", self.field, value)

    def isnot(self, value):
        return _Expr("isnot", self.field, value)


def _expr_contains(expr, field, value):
    if isinstance(expr, _Expr):
        if expr.field == field and expr.value == value:
            return True
        return any(_expr_contains(child, field, value) for child in expr.children)
    return False


class _CalendarCal:
    id = _Column("CalendarCal.id")
    owner = _Column("CalendarCal.owner")
    name = _Column("CalendarCal.name")


class _CalendarEvent:
    uid = _Column("CalendarEvent.uid")
    status = _Column("CalendarEvent.status")
    rrule = _Column("CalendarEvent.rrule")
    dtstart = _Column("CalendarEvent.dtstart")
    dtend = _Column("CalendarEvent.dtend")
    calendar_id = _Column("CalendarEvent.calendar_id")


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filter_calls = []
        self.owner_filter = None
        self.all_called = False

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *exprs):
        self.filter_calls.append(exprs)
        for expr in exprs:
            if _expr_contains(expr, "CalendarCal.owner", "alice"):
                self.owner_filter = "alice"
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        self.all_called = True
        if self.owner_filter is None:
            return list(self.rows)
        return [
            row for row in self.rows
            if getattr(getattr(row, "calendar", None), "owner", None) == self.owner_filter
        ]


class _FakeSession:
    def __init__(self, *, calendars=(), events=()):
        self.calendar_query = _FakeQuery(list(calendars))
        self.event_query = _FakeQuery(list(events))
        self.add = MagicMock()
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.close = MagicMock()

    def query(self, model):
        if model is _CalendarCal:
            return self.calendar_query
        if model is _CalendarEvent:
            return self.event_query
        raise AssertionError(f"unexpected query model: {model!r}")


def _install_calendar_db_stub(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.CalendarCal = _CalendarCal
    db.CalendarEvent = _CalendarEvent
    for name in [
        "Base",
        "Document",
        "DocumentVersion",
        "Session",
        "ChatMessage",
        "GalleryImage",
        "GalleryAlbum",
        "Note",
        "ScheduledTask",
        "TaskRun",
        "ModelEndpoint",
        "Webhook",
    ]:
        setattr(db, name, MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)
    return db


def _install_multipart_stub(monkeypatch):
    multipart = types.ModuleType("python_multipart")
    multipart.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", multipart)


def _import_calendar_routes(monkeypatch):
    _install_calendar_db_stub(monkeypatch)
    _install_multipart_stub(monkeypatch)
    monkeypatch.delitem(sys.modules, "routes.calendar_routes", raising=False)
    mod = __import__("routes.calendar_routes", fromlist=["setup_calendar_routes"])
    monkeypatch.setattr(mod, "or_", lambda *args: _Expr("or", children=args))
    monkeypatch.setattr(mod, "and_", lambda *args: _Expr("and", children=args))
    return mod


def _route_endpoint(calendar_routes, path, method):
    router = calendar_routes.setup_calendar_routes()
    full_path = f"/api/calendar{path}"
    for route in router.routes:
        if route.path == full_path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full_path}")


def _request(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _calendar(owner, cal_id="cal-target"):
    return SimpleNamespace(id=cal_id, owner=owner, name=f"{owner or 'null'} calendar")


def _event(owner, uid):
    return SimpleNamespace(
        uid=uid,
        calendar=_calendar(owner, cal_id=f"{owner or 'null'}-cal"),
        calendar_id=f"{owner or 'null'}-cal",
        dtstart=SimpleNamespace(isoformat=lambda: f"{uid}-start"),
        dtend=SimpleNamespace(isoformat=lambda: f"{uid}-end"),
        summary=uid,
        description="",
        location="",
        all_day=False,
        is_utc=False,
        rrule="",
        color=None,
        event_type=None,
        importance="normal",
    )


def test_create_event_rejects_null_owner_calendar_href_at_route_boundary(monkeypatch):
    calendar_routes = _import_calendar_routes(monkeypatch)
    session = _FakeSession(calendars=[_calendar(None)])
    monkeypatch.setattr(calendar_routes, "SessionLocal", lambda: session)
    create_event = _route_endpoint(calendar_routes, "/events", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_event(
            _request(),
            calendar_routes.EventCreate(
                summary="blocked",
                dtstart="2026-06-02T10:00:00",
                calendar_href="cal-target",
            ),
        ))

    assert exc.value.status_code == 404
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.close.assert_called_once()


def test_create_event_rejects_cross_owner_calendar_href_at_route_boundary(monkeypatch):
    calendar_routes = _import_calendar_routes(monkeypatch)
    session = _FakeSession(calendars=[_calendar("bob")])
    monkeypatch.setattr(calendar_routes, "SessionLocal", lambda: session)
    create_event = _route_endpoint(calendar_routes, "/events", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_event(
            _request(),
            calendar_routes.EventCreate(
                summary="blocked",
                dtstart="2026-06-02T10:00:00",
                calendar_href="cal-target",
            ),
        ))

    assert exc.value.status_code == 404
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.close.assert_called_once()


def test_list_events_filters_by_calendar_owner_before_output(monkeypatch):
    calendar_routes = _import_calendar_routes(monkeypatch)
    session = _FakeSession(events=[
        _event(None, "null-owner"),
        _event("bob", "bob-event"),
        _event("alice", "alice-event"),
    ])
    monkeypatch.setattr(calendar_routes, "SessionLocal", lambda: session)

    expanded = []

    def fake_expand(event, _start, _end):
        assert event.calendar.owner == "alice"
        expanded.append(event.uid)
        return [{"uid": event.uid, "dtstart": "2026-06-02T10:00:00"}]

    monkeypatch.setattr(calendar_routes, "_expand_rrule", fake_expand)
    list_events = _route_endpoint(calendar_routes, "/events", "GET")

    out = asyncio.run(list_events(
        _request(),
        start="2026-06-01T00:00:00",
        end="2026-06-03T00:00:00",
    ))

    assert out == {"events": [{"uid": "alice-event", "dtstart": "2026-06-02T10:00:00"}]}
    assert expanded == ["alice-event"]
    assert session.event_query.owner_filter == "alice"
    session.close.assert_called_once()


def test_export_ics_rejects_null_owner_calendar_at_route_boundary(monkeypatch):
    calendar_routes = _import_calendar_routes(monkeypatch)
    session = _FakeSession(calendars=[_calendar(None)])
    monkeypatch.setattr(calendar_routes, "SessionLocal", lambda: session)
    export_ics = _route_endpoint(calendar_routes, "/export/{cal_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(export_ics(_request(), cal_id="cal-target"))

    assert exc.value.status_code == 404
    assert not session.event_query.all_called
    session.close.assert_called_once()


def test_export_ics_rejects_cross_owner_calendar_at_route_boundary(monkeypatch):
    calendar_routes = _import_calendar_routes(monkeypatch)
    session = _FakeSession(calendars=[_calendar("bob")])
    monkeypatch.setattr(calendar_routes, "SessionLocal", lambda: session)
    export_ics = _route_endpoint(calendar_routes, "/export/{cal_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(export_ics(_request(), cal_id="cal-target"))

    assert exc.value.status_code == 404
    assert not session.event_query.all_called
    session.close.assert_called_once()
