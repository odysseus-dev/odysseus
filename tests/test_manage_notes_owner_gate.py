import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from src import tool_implementations


class _Query:
    def __init__(self, note):
        self.note = note

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.note


class _Db:
    def __init__(self, note):
        self.note = note
        self.deleted = []
        self.commits = 0

    def query(self, *args, **kwargs):
        return _Query(self.note)

    def delete(self, note):
        self.deleted.append(note)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _install_fakes(monkeypatch, note):
    fake_sa_attrs = types.ModuleType("sqlalchemy.orm.attributes")
    fake_sa_attrs.flag_modified = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", fake_sa_attrs)

    db = _Db(note)
    fake_core_db = types.ModuleType("core.database")
    fake_core_db.SessionLocal = lambda: db
    fake_core_db.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", fake_core_db)
    return db


def _run(args, owner="alice"):
    return asyncio.run(tool_implementations.do_manage_notes(json.dumps(args), owner=owner))


def _note(owner=None, **overrides):
    data = {
        "id": "abc12345-existing",
        "owner": owner,
        "title": "Original",
        "content": "",
        "note_type": "note",
        "color": None,
        "label": None,
        "items": '[{"text":"item","done":false}]',
        "pinned": False,
        "archived": False,
        "due_date": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_update_rejects_legacy_null_owner_for_authenticated_owner(monkeypatch):
    note = _note(owner=None)
    db = _install_fakes(monkeypatch, note)

    result = _run({"action": "update", "id": "abc12345", "title": "Changed"})

    assert result == {"error": "Note not found", "exit_code": 1}
    assert note.title == "Original"
    assert db.commits == 0


def test_delete_rejects_legacy_empty_owner_for_authenticated_owner(monkeypatch):
    note = _note(owner="")
    db = _install_fakes(monkeypatch, note)

    result = _run({"action": "delete", "id": "abc12345"})

    assert result == {"error": "Note not found", "exit_code": 1}
    assert db.deleted == []
    assert db.commits == 0


def test_toggle_rejects_other_owner(monkeypatch):
    note = _note(owner="bob")
    db = _install_fakes(monkeypatch, note)

    result = _run({"action": "toggle_item", "id": "abc12345", "index": 0})

    assert result == {"error": "Note not found", "exit_code": 1}
    assert json.loads(note.items)[0]["done"] is False
    assert db.commits == 0


def test_update_allows_matching_owner(monkeypatch):
    note = _note(owner="alice")
    db = _install_fakes(monkeypatch, note)

    result = _run({"action": "update", "id": "abc12345", "title": "Changed"})

    assert result["exit_code"] == 0
    assert note.title == "Changed"
    assert db.commits == 1
