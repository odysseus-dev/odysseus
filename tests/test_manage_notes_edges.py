"""manage_notes branches no other test reaches.

The one with weight: a run's write re-checks the note after reading it back,
so a save the user made between the run's check and its write is refused, not
overwritten. Then the small ones: a tick on a note without tasks, the archived
list, the duplicate check on `add`, the bound on what the tool remembers and
a gate payload that is not JSON. Same harness as test_note_agent_edit_scope.
"""
import asyncio
import json

from core.database import Note
from core.note_agent_scope import bind_note_edit_scope, unbind_note_edit_scope
from src.tool_capabilities import ToolRunSecurityContext
from src.tools import notes as notes_tool
from tests.test_note_agent_edit_scope import MINE, OTHER, _read, _row, _run, _scope, _user_edits, db  # noqa: F401  (fixture)


def _plain(args, owner="alice"):
    return asyncio.run(notes_tool.do_manage_notes(json.dumps(args), owner=owner))


def test_a_save_landing_between_the_runs_check_and_its_write_is_not_overwritten(db, monkeypatch):
    scope = _scope(db)
    _read(db, scope)
    real_stamp = notes_tool.stored_stamp
    landed = []

    def user_saves_now(session, note_id):
        # The user's save lands after the tool checked the version it read,
        # just before it reads the row back to write.
        if not landed:
            landed.append(note_id)
            _user_edits(db, note_id, lambda c: c + "\ntyped by the user")
        return real_stamp(session, note_id)

    monkeypatch.setattr(notes_tool, "stored_stamp", user_saves_now)
    result = _run({"action": "update", "id": MINE, "content": "agent rewrite"}, scope)
    assert landed == [MINE]
    assert result["exit_code"] == 1, result
    assert "changed since you last read it" in result["error"]
    assert _row(db, MINE).content.endswith("typed by the user")


def test_ticking_a_note_without_tasks_says_so(db):
    with db() as s:
        s.add(Note(id="prose0001", owner="alice", title="Prose", content="just words"))
        s.commit()
    assert _plain({"action": "view", "id": "prose0001"})["exit_code"] == 0
    result = _plain({"action": "toggle_item", "id": "prose0001", "index": 0})
    assert result == {"error": "Note has no checklist items", "exit_code": 1}


def test_the_archived_list_is_only_my_archive(db):
    with db() as s:
        s.add(Note(id="arch0001", owner="alice", title="Old trip", content="x", archived=True))
        s.add(Note(id="arch0002", owner="bob", title="Bob's old", content="y", archived=True))
        s.commit()
    listed = _plain({"action": "list", "archived": True})
    assert "Old trip" in listed["results"] and "Bob's old" not in listed["results"]


def test_add_returns_the_existing_note_only_when_everything_matches(db):
    first = _plain({"action": "add", "title": "Hardware store", "content": "nails"})
    assert first["exit_code"] == 0
    again = _plain({"action": "add", "title": "Hardware store", "content": "nails"})
    assert again.get("note_id") and "already exists" in again["response"]
    other = _plain({"action": "add", "title": "Hardware store", "content": "screws"})
    assert other["exit_code"] == 0 and "already exists" not in other.get("response", "")
    with db() as s:
        assert s.query(Note).filter(Note.title == "Hardware store").count() == 2


def test_what_the_tool_remembers_reading_is_bounded(db, monkeypatch):
    monkeypatch.setattr(notes_tool, "_SEEN_VERSIONS_MAX", 1)
    _plain({"action": "view", "id": MINE})
    _plain({"action": "view", "id": OTHER})
    assert list(notes_tool._SEEN_VERSIONS) == [("alice", OTHER)]


def test_a_gate_payload_that_is_not_json_is_read_as_empty(db):
    scope = _scope(db)
    gate = ToolRunSecurityContext(external_untrusted_context_seen=True)
    token = bind_note_edit_scope(scope)
    try:
        empty = gate.decision_for("manage_notes", "{}")
        for content in ("not json at all", "[1, 2]", "   "):
            # Read as an empty request, never an error inside the gate.
            assert gate.decision_for("manage_notes", content).allowed == empty.allowed, content
    finally:
        unbind_note_edit_scope(token)


def test_a_second_reminder_for_the_same_title_and_time_is_the_first_one(db):
    # A calendar event with a reminder, then the model also asking for a note
    # reminder: one dispatch, not two.
    first = _plain({"action": "add", "title": "Dentist", "due_date": "2030-01-02T09:00:00+00:00"})
    assert first["exit_code"] == 0
    second = _plain({"action": "add", "title": "dentist ", "content": "bring the forms", "due_date": "2030-01-02T09:00:00+00:00"})
    assert second.get("duplicate") is True and "Reminder already exists" in second["response"], second
    with db() as s:
        assert s.query(Note).filter(Note.title.ilike("dentist%")).count() == 1
    # Another time is another reminder.
    third = _plain({"action": "add", "title": "Dentist", "due_date": "2030-02-02T09:00:00+00:00"})
    assert third["exit_code"] == 0 and not third.get("duplicate")
