"""manage_notes writes keep what the user did while a run was going.

`checklist_items` is the whole task list: written over a note that changed
since the model last saw it, it would drop the ticks and tasks the user
added in the meantime, so it is refused until the model looks again. And a
tool write that loses a race to the user's own write saves nothing rather
than overwriting it.
"""
import asyncio
import datetime
import json

import pytest

import core.database as cdb
from core.database import Note
from src.tools import notes as notes_tool
from tests.helpers.database import disposable_database

_ID = "trip0001-note"


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(notes_tool, "_SEEN_VERSIONS", type(notes_tool._SEEN_VERSIONS)())

        def seed(content):
            with factory() as db:
                db.add(Note(id=_ID, owner="alice", title="Trip", content=content, note_type="note"))
                db.commit()

        def content():
            with factory() as db:
                return db.get(Note, _ID).content

        def user_edits(change):
            """The user ticks or adds something in the Notes window."""
            with factory() as db:
                row = db.get(Note, _ID)
                row.content = change(row.content)
                row.updated_at = row.updated_at + datetime.timedelta(seconds=1)
                db.commit()

        yield seed, content, user_edits


def _run(args, owner="alice"):
    return asyncio.run(notes_tool.do_manage_notes(json.dumps(args), owner=owner))




def test_checklist_over_a_note_changed_since_view_is_refused(notes):
    seed, content, user_edits = notes
    seed("- [ ] a\n- [ ] b")
    assert _run({"action": "view", "id": _ID})["exit_code"] == 0
    user_edits(lambda c: c.replace("- [ ] b", "- [x] b") + "\n- [ ] c from the user")
    result = _run({"action": "update", "id": _ID, "checklist_items": [
        {"text": "a", "done": True}, {"text": "b", "done": False}]})
    assert result["exit_code"] == 1
    assert "changed since you last viewed it" in result["error"]
    assert content() == "- [ ] a\n- [x] b\n- [ ] c from the user"
    # After a fresh view the same kind of write goes through.
    assert _run({"action": "view", "id": _ID})["exit_code"] == 0
    result = _run({"action": "update", "id": _ID, "checklist_items": [
        {"text": "a", "done": True}, {"text": "b", "done": True}, {"text": "c from the user", "done": False}]})
    assert result["exit_code"] == 0
    assert content() == "- [x] a\n- [x] b\n- [ ] c from the user"


def test_consecutive_tool_writes_do_not_trip_the_check(notes):
    seed, content, _ = notes
    seed("- [ ] a")
    _run({"action": "view", "id": _ID})
    assert _run({"action": "toggle_item", "id": _ID, "index": 0})["exit_code"] == 0
    result = _run({"action": "update", "id": _ID, "checklist_items": [
        {"text": "a", "done": True}, {"text": "b", "done": False}]})
    assert result["exit_code"] == 0
    assert content() == "- [x] a\n- [ ] b"


def test_tool_update_losing_a_race_saves_nothing(notes, monkeypatch):
    seed, content, user_edits = notes
    seed("- [ ] a")
    real_stamp = notes_tool.stored_stamp

    def stamp_then_user_edits(db, note_id):
        stamp = real_stamp(db, note_id)
        user_edits(lambda c: c + "\n- [ ] typed meanwhile")
        return stamp

    monkeypatch.setattr(notes_tool, "stored_stamp", stamp_then_user_edits)
    result = _run({"action": "update", "id": _ID, "title": "Trip (packed)"})
    assert result["exit_code"] == 1
    assert content() == "- [ ] a\n- [ ] typed meanwhile"
