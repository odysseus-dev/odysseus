"""Regression: checklist items supplied as plain strings must not crash.

The manage_notes tool schema advertises `checklist_items` as a list of
{text, done} objects, but local / open-source models routinely ignore the
object schema and emit a bare list of strings, e.g.

    {"action": "add", "note_type": "checklist",
     "checklist_items": ["buy milk", "walk dog"]}

`do_manage_notes` stored that verbatim (`json.dumps(items_raw)`), so the
resulting note's `items` JSON was `["buy milk", "walk dog"]`. Every
downstream consumer then assumed each item is a dict and called
`item.get(...)`:
  * the `list` action  (item.get("done"))
  * the `toggle_item` action  (items[index].get("done"))
  * the HTTP route POST /api/notes/{id}/items/{i}/toggle (500s outright)
All of those raise AttributeError on a `str` item.

Fix: normalize string items to {"text": ..., "done": False} when the note
is written, so the stored shape always matches what consumers expect.
"""
import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from src import tool_implementations


def _install_fakes(monkeypatch, note=None):
    fake_sa_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_sa_attrs.flag_modified = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_sa_attrs)

    added = {}

    class FakeQuery:
        def filter(self, *a, **k):
            return self

        def first(self):
            return note

    class FakeDB:
        def query(self, *a, **k):
            return FakeQuery()

        def add(self, obj):
            added["note"] = obj

        def commit(self):
            pass

        def refresh(self, *a, **k):
            pass

        def close(self):
            pass

    fake_core_db = types.ModuleType("core.database")
    fake_core_db.SessionLocal = lambda: FakeDB()
    # Note is only used as a query/filter argument and as a constructor in
    # the `add` path. MagicMock satisfies both (Note.id.startswith(...) and
    # Note(**kwargs)); we capture the constructed note via FakeDB.add().
    note_cls = MagicMock()
    note_cls.side_effect = lambda **kw: SimpleNamespace(**kw)
    fake_core_db.Note = note_cls
    monkeypatch.setitem(sys.modules, "core.database", fake_core_db)
    return added


def _run(args):
    return asyncio.run(tool_implementations.do_manage_notes(json.dumps(args), owner=None))


def test_add_coerces_string_checklist_items_to_dicts(monkeypatch):
    added = _install_fakes(monkeypatch)
    result = _run({
        "action": "add",
        "title": "Groceries",
        "note_type": "checklist",
        "checklist_items": ["buy milk", "walk dog"],
    })
    assert result.get("exit_code") == 0
    stored = json.loads(added["note"].items)
    # Every stored item must be a {text, done} dict, never a bare string.
    assert all(isinstance(it, dict) for it in stored), stored
    assert stored[0]["text"] == "buy milk" and stored[0]["done"] is False
    assert stored[1]["text"] == "walk dog"


def test_toggle_item_survives_string_items(monkeypatch):
    # A note whose items were persisted as plain strings (pre-fix data, or an
    # add that bypassed normalization) must still toggle without crashing.
    note = SimpleNamespace(
        id="abc12345-existing", owner=None, title="Groceries", content=None,
        note_type="checklist", color=None, label=None,
        items=json.dumps(["buy milk", "walk dog"]),
        pinned=False, archived=False, due_date=None,
    )
    _install_fakes(monkeypatch, note=note)
    result = _run({"action": "toggle_item", "id": "abc12345", "index": 0})
    assert result.get("exit_code") == 0, result
    items = json.loads(note.items)
    assert items[0].get("done") is True
