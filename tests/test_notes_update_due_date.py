"""Regression: manage_notes `update` must parse due_date like `add` does.

The `add` action runs due_date through `parse_due_for_user` (natural language
like "tomorrow at 9am", plus user-tz anchoring for naive ISO). The `update`
action stored the raw value verbatim, so a reminder edited with natural language
was saved as an unparseable literal the frontend's `new Date()` can't read — and
the reminder never fired. Both actions must route due_date through the parser.
"""
import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import tool_implementations


def _install_fakes(monkeypatch, note, parse=None):
    """Stub the modules do_manage_notes imports lazily at call time.

    core.database opens a real sqlite file and routes.calendar_routes needs
    dateutil, so we inject light fakes. We also pin sqlalchemy.orm.attributes
    (for flag_modified): it imports fine in isolation, but other tests in the
    suite replace sys.modules['sqlalchemy.orm'] with a non-package, so we make
    this leaf import order-independent. Placing each leaf module in sys.modules
    means the parent package is never re-imported.
    """
    fake_sa_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_sa_attrs.flag_modified = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_sa_attrs)

    class FakeQuery:
        def filter(self, *a, **k):
            return self

        def first(self):
            return note

    class FakeDB:
        def query(self, *a, **k):
            return FakeQuery()

        def add(self, *a, **k):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    fake_core_db = types.ModuleType("core.database")
    fake_core_db.SessionLocal = lambda: FakeDB()
    fake_core_db.Note = MagicMock()  # only used as a query/filter argument
    monkeypatch.setitem(sys.modules, "core.database", fake_core_db)

    calls = {"parsed": []}

    def _default_parse(s):
        calls["parsed"].append(s)
        return "PARSED::" + s

    fake_cal = types.ModuleType("routes.calendar_routes")
    fake_cal.parse_due_for_user = parse or _default_parse
    monkeypatch.setitem(sys.modules, "routes.calendar_routes", fake_cal)
    return calls


def _run_update(args):
    return asyncio.run(tool_implementations.do_manage_notes(json.dumps(args), owner=None))


def test_update_parses_natural_language_due_date(monkeypatch):
    note = SimpleNamespace(
        id="abc12345-existing", owner=None, title="Dentist", content=None,
        note_type="note", color=None, label=None, items=None,
        pinned=False, archived=False, due_date=None,
    )
    calls = _install_fakes(monkeypatch, note)

    result = _run_update(
        {"action": "update", "id": "abc12345", "due_date": "tomorrow at 9am"}
    )

    assert result.get("exit_code") == 0
    # Stored value went through the parser, not the raw literal.
    assert note.due_date == "PARSED::tomorrow at 9am"
    assert calls["parsed"] == ["tomorrow at 9am"]


def test_update_still_sets_other_fields_without_parsing_them(monkeypatch):
    note = SimpleNamespace(
        id="abc12345-existing", owner=None, title="Old", content=None,
        note_type="note", color=None, label=None, items=None,
        pinned=False, archived=False, due_date=None,
    )
    calls = _install_fakes(monkeypatch, note)

    result = _run_update(
        {"action": "update", "id": "abc12345", "title": "New", "label": "home"}
    )

    assert result.get("exit_code") == 0
    assert note.title == "New"
    assert note.label == "home"
    # No due_date supplied → the parser is not invoked.
    assert calls["parsed"] == []
