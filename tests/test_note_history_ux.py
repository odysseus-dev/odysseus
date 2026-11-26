"""What the history says about each change: a one-line summary, the
chat's name, the count on the note, and the version a restore kept.

Same harness as test_note_versions: manage_notes runs as the agent would and
the notes API runs over a real database.
"""
import pytest

from core.database import NoteShare, Session as DbSession
from core.note_versions import describe_change
from tests.test_note_versions import (  # noqa: F401  (fixtures)
    NOTE, OTHER_CHAT, PLAIN_CHAT, SCOPED_CHAT, _agent, _plain_agent, api, db, uploads,
)

BASE = {"title": "Trip", "content": "Pack light\n- [ ] book the cabin", "items": None, "color": None,
        "label": None, "pinned": False, "due_date": None, "note_type": None}


def _with(**changes):
    return {**BASE, **changes}


@pytest.mark.parametrize("after, summary", [
    (_with(content="Pack light\n- [x] book the cabin"), "ticked “book the cabin”"),
    (_with(content="Pack light\n- [ ] book the cabin\nBring a rain jacket"), "added 1 line"),
    (_with(content="Pack light\n- [ ] book the cabin\n- [ ] buy fuel"), "added the task “buy fuel”"),
    (_with(content="- [ ] book the cabin"), "removed 1 line"),
    (_with(content="Pack heavy\n- [ ] book the cabin"), "edited the text"),
    (_with(title="Cabin trip"), "renamed the note"),
    (_with(color="red"), "changed the colour"),
    (_with(label="travel"), "changed the tag"),
    (_with(due_date="2026-12-01T09:00"), "set a reminder"),
    (_with(pinned=True), "pinned the note"),
    (_with(title="Cabin trip", content="Pack light\n- [x] book the cabin"),
     "renamed the note, ticked “book the cabin”"),
])
def test_describe_change_says_what_happened(after, summary):
    assert describe_change(BASE, after) == summary


def test_a_long_task_is_quoted_short():
    long = "book the cabin by the lake before the end of the month please"
    before = _with(content=f"- [ ] {long}")
    said = describe_change(before, _with(content=f"- [x] {long}"))
    assert said.startswith("ticked “book the cabin") and said.endswith("…”")


def test_each_row_says_what_the_change_after_it_did(db, api):
    assert _agent(db, {"action": "update", "content": "Pack light\n- [ ] book the cabin\nBring a rain jacket"})["exit_code"] == 0
    assert _agent(db, {"action": "toggle_item", "index": 0}, chat=OTHER_CHAT)["exit_code"] == 0
    assert _plain_agent({"action": "update", "title": "Cabin trip"})["exit_code"] == 0
    rows = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    # Newest first: the rename (to the note as it is now), the tick, the added line.
    assert [r["summary"] for r in rows] == [
        "renamed the note", "ticked “book the cabin”", "added 1 line"]



def test_a_version_read_carries_the_note_its_change_left(db, api):
    """The History shows a change as it happened (before it, then after it), not as its undo."""
    assert _agent(db, {"action": "update", "content": "Pack light\n- [ ] book the cabin\nBring a rain jacket"})["exit_code"] == 0
    assert _agent(db, {"action": "update", "content": "Pack light"})["exit_code"] == 0
    newest, oldest = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    assert (newest["summary"], oldest["summary"]) == ("removed 1 line", "added 1 line")
    first = api("GET", f"/api/notes/{NOTE}/versions/{oldest['id']}").json()
    # The older change goes to the next newer version, not to the note as it is now.
    assert first["content"] == "Pack light\n- [ ] book the cabin"
    assert first["after"]["content"] == "Pack light\n- [ ] book the cabin\nBring a rain jacket"
    assert first["after"]["content"] == api("GET", f"/api/notes/{NOTE}/versions/{newest['id']}").json()["content"]
    last = api("GET", f"/api/notes/{NOTE}/versions/{newest['id']}").json()
    # The newest change goes to the note as it is now (the agent's text keeps the task line).
    assert last["after"]["content"] == api("GET", f"/api/notes/{NOTE}").json()["content"]
    assert "Bring a rain jacket" not in last["after"]["content"]
    assert last["after"]["title"] == "Trip" and last["after"]["pinned"] is False

def test_a_row_names_its_chat_and_a_deleted_chat_is_not_named(db, api):
    assert _agent(db, {"action": "update", "content": "one"})["exit_code"] == 0
    assert _plain_agent({"action": "update", "title": "two"})["exit_code"] == 0
    with db() as s:
        # Someone else's chat with the same id must not lend its name either.
        s.add(DbSession(id="bobs-chat", name="Bob's secrets", endpoint_url="", model="m", owner="bob"))
        s.commit()
    rows = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    plain, scoped = rows
    assert (scoped["chat_title"], scoped["chat_exists"]) == ("Agent: Trip", True)
    # PLAIN_CHAT has no sessions row: deleted, so no name and no link.
    assert (plain["session_id"], plain["chat_title"], plain["chat_exists"]) == (PLAIN_CHAT, None, False)
    with db() as s:
        s.delete(s.get(DbSession, SCOPED_CHAT))
        s.commit()
    rows = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    assert (rows[1]["chat_title"], rows[1]["chat_exists"]) == (None, False)


def test_another_accounts_chat_title_is_never_shown(db, api):
    with db() as s:
        s.add(DbSession(id=PLAIN_CHAT, name="Bob's secrets", endpoint_url="", model="m", owner="bob"))
        s.commit()
    assert _plain_agent({"action": "update", "title": "two"})["exit_code"] == 0
    [row] = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    assert (row["chat_title"], row["chat_exists"]) == (None, False)


def test_the_note_carries_its_version_count_in_the_list_and_alone(db, api):
    assert api("GET", f"/api/notes/{NOTE}").json()["version_count"] == 0
    assert _agent(db, {"action": "update", "content": "one"})["exit_code"] == 0
    assert _agent(db, {"action": "update", "content": "two"})["exit_code"] == 0
    assert api("GET", f"/api/notes/{NOTE}").json()["version_count"] == 2
    [listed] = [n for n in api("GET", "/api/notes").json()["notes"] if n["id"] == NOTE]
    assert listed["version_count"] == 2


def test_a_collaborator_does_not_get_the_count(db, api):
    assert _agent(db, {"action": "update", "content": "one"})["exit_code"] == 0
    with db() as s:
        s.add(NoteShare(id="g1", note_id=NOTE, shared_with="bob", permission="edit"))
        s.commit()
    assert api("GET", f"/api/notes/{NOTE}", user="bob").json()["version_count"] is None


def test_a_restore_says_which_version_undoes_it(db, api):
    assert _agent(db, {"action": "update", "content": "Bring boots"})["exit_code"] == 0
    [v] = api("GET", f"/api/notes/{NOTE}/versions").json()["versions"]
    note = api("GET", f"/api/notes/{NOTE}").json()
    restored = api("POST", f"/api/notes/{NOTE}/versions/{v['id']}/restore",
                   json={"base_updated_at": note["updated_at"]}).json()
    assert restored["content"] == "Pack light\n- [ ] book the cabin"
    undo_id = restored["undo_version_id"]
    assert undo_id and undo_id != v["id"]
    # Restoring that one brings the agent's text back: the restore is undone.
    back = api("POST", f"/api/notes/{NOTE}/versions/{undo_id}/restore",
               json={"base_updated_at": restored["updated_at"]})
    assert back.status_code == 200
    assert back.json()["content"].startswith("Bring boots")
