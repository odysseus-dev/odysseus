import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from src import tool_implementations


class _Query:
    def __init__(self, notes):
        self.notes = notes

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self.notes

    def first(self):
        return self.notes[0] if self.notes else None


class _Db:
    def __init__(self, notes):
        self.notes = notes
        self.added = []
        self.commits = 0

    def query(self, *args, **kwargs):
        return _Query(self.notes)

    def add(self, note):
        self.added.append(note)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _note(**overrides):
    data = {
        "id": "abc12345-existing",
        "owner": None,
        "title": "Farmers market packing",
        "content": "private body marker",
        "note_type": "checklist",
        "color": None,
        "label": "market",
        "items": json.dumps([{"text": "Square reader", "done": False}]),
        "pinned": False,
        "archived": False,
        "due_date": None,
        "source": "agent",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_search_returns_locator_only_and_view_returns_body(monkeypatch):
    note = _note()
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: _Db([note])
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    search = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "search", "query": "Farmers market packing"}),
        owner=None,
    ))
    assert "abc12345" in search["results"]
    assert "Square reader" not in search["results"]
    assert "private body marker" not in search["results"]

    view = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "view", "id": "abc12345"}),
        owner=None,
    ))
    assert "Square reader" in view["results"]


def test_list_with_search_field_is_treated_as_search(monkeypatch):
    matching = _note(
        id="abc12345-existing",
        title="ODY-EVAL-TOOL-NOTES-SEARCH",
        content="private body marker",
    )
    other = _note(id="def67890-other", title="Japan", content="unrelated")
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: _Db([matching, other])
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    result = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "list", "search": "ODY-EVAL-TOOL-NOTES-SEARCH"}),
        owner=None,
    ))

    assert "ODY-EVAL-TOOL-NOTES-SEARCH" in result["results"]
    assert "Japan" not in result["results"]
    assert "private body marker" not in result["results"]


def test_search_matches_meaningful_tokens_when_phrase_skips_words(monkeypatch):
    matching = _note(
        id="suica123-existing",
        title="Tokyo packing idea: umbrella and Suica",
        content="Tokyo packing idea: umbrella and Suica",
        note_type="note",
        label="travel",
        items=None,
    )
    other = _note(id="def67890-other", title="Tokyo dinner reservation", content="unrelated")
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: _Db([matching, other])
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    result = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "search", "query": "Tokyo packing idea Suica"}),
        owner=None,
    ))

    assert "Tokyo packing idea: umbrella and Suica" in result["results"]
    assert "Tokyo dinner reservation" not in result["results"]


def test_search_matches_singular_and_plural_terms_across_label_and_title(monkeypatch):
    matching = _note(
        id="contractor-existing",
        title="Contractor list",
        content="Extension status",
        note_type="note",
        label="forecast",
        items=None,
    )
    other = _note(id="other-note", title="Forecast budget", content="No vendors")
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: _Db([matching, other])
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    result = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "search", "query": "Forecast Contractors"}),
        owner=None,
    ))

    assert "Contractor list" in result["results"]
    assert "Forecast budget" not in result["results"]


def test_list_hides_calendar_reminder_notes_by_default(monkeypatch):
    regular = _note(id="abc12345-existing", title="Real user note")
    calendar_reminder = _note(
        id="cal12345-reminder",
        title="Calendar reminder: Kindergarten pickup",
        label="calendar",
        source="calendar",
        due_date="2030-03-01T14:45:00Z",
    )
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: _Db([regular, calendar_reminder])
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    hidden = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "list"}),
        owner=None,
    ))
    assert "Real user note" in hidden["results"]
    assert "Kindergarten pickup" not in hidden["results"]

    included = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "list", "include_calendar_reminders": True}),
        owner=None,
    ))
    assert "Real user note" in included["results"]
    assert "Kindergarten pickup" in included["results"]


def test_view_accepts_note_id_aliases(monkeypatch):
    note = _note(id="abc12345-existing")
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: _Db([note])
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    snake = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "view", "note_id": "abc12345"}),
        owner=None,
    ))
    camel = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "view", "noteId": "abc12345"}),
        owner=None,
    ))

    assert "Square reader" in snake["results"]
    assert "Square reader" in camel["results"]


def test_add_returns_existing_exact_duplicate_instead_of_creating(monkeypatch):
    note = _note(
        id="dupe1234-existing",
        owner="alice",
        title="SFT smoke packing list",
        content=None,
        label="travel",
        note_type="checklist",
        items=json.dumps([
            {"text": "passport", "done": False},
            {"text": "charger", "done": False},
        ]),
    )
    fake_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_attrs)
    db = _Db([note])
    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = lambda: db
    fake_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_db)

    result = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({
            "action": "add",
            "title": " SFT smoke packing   list ",
            "label": "travel",
            "note_type": "checklist",
            "items": [
                {"text": "passport", "done": False},
                {"text": "charger", "done": False},
            ],
        }),
        owner="alice",
    ))

    assert result["exit_code"] == 0
    assert result["duplicate"] is True
    assert result["note_id"] == "dupe1234-existing"
    assert db.added == []
    assert db.commits == 0
