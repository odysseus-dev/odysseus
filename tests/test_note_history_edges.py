"""Edges of the note history, the edit scope and the conditional note write.

Sentences the history has for changes no other test makes, stored data that
is damaged, the bounded memory of what each run last read, and a write that
loses every retry. (A row with no `updated_at` cannot be made on the current
schema, so `commit_if_unchanged`'s legacy null-stamp branch stays unreached.) Unit level, plus a disposable database where a row is needed.
"""
import datetime
import json

import pytest

import core.note_agent_scope as scope_mod
import core.note_versions as versions_mod
from core.database import Note, NoteVersion, Session as DbSession
from core.note_agent_scope import NoteEditScope, load_note_edit_scope, parse_undo, set_session_note_scope, shared_note_ids
from core.note_versions import describe_change
from core.note_writes import NoteChanged, write_with_retry
from tests.helpers.database import disposable_database

BASE = {"title": "Trip", "content": "Pack light\n- [ ] book the cabin\n- [ ] buy fuel", "items": None, "color": None,
        "label": None, "pinned": False, "due_date": None, "note_type": None}


def _with(**changes):
    return {**BASE, **changes}


@pytest.mark.parametrize("before, after, summary", [
    (BASE, _with(content="Pack light\n- [x] book the cabin\n- [x] buy fuel"), "ticked 2 tasks"),
    (_with(content="- [x] a\n- [x] b"), _with(content="- [ ] a\n- [ ] b"), "unticked 2 tasks"),
    (BASE, _with(note_type="goal"), "changed the type"),
    (BASE, _with(content="Pack heavy\n- [ ] rent the cabin\n- [ ] buy fuel"), "edited the text"),
    (BASE, _with(content="Pack light\n- [ ] book the cabin\n- [ ] buy fuel\nOne\nTwo"), "added 2 lines"),
    (BASE, _with(content="Pack light"), "removed 2 lines"),
    (BASE, _with(content="Pack light\n- [ ] book the cabin"), "removed the task “buy fuel”"),
    (_with(due_date="2026-12-01T09:00"), BASE, "removed the reminder"),
    (_with(due_date="2026-12-01T09:00"), _with(due_date="2026-12-02T09:00"), "changed the reminder"),
    (_with(pinned=True), BASE, "unpinned the note"),
    (BASE, BASE, "changed the note"),
])
def test_the_history_names_each_kind_of_change(before, after, summary):
    assert describe_change(before, after) == summary


def test_a_damaged_version_reads_as_empty_and_a_long_text_is_cut_in_the_preview():
    broken = NoteVersion(id=1, note_id="n", fields="{not json", source="user", digest="x")
    assert set(versions_mod.version_fields(broken).values()) == {None}, "every field reads as empty"
    assert versions_mod.version_fields(NoteVersion(id=2, note_id="n", fields="[1, 2]", source="user", digest="x")) == {}
    long = NoteVersion(id=3, note_id="n", source="user", digest="x",
                       fields=json.dumps({"content": "word " * 200, "title": "Long"}))
    view = versions_mod.version_view(long, Note(id="n", owner="alice"))
    assert len(view["preview"]) == versions_mod.NOTE_VERSION_PREVIEW_CHARS
    assert view["preview"].endswith("…")


def test_no_ids_means_no_counts_and_nothing_deleted():
    assert versions_mod.version_counts(None, []) == {}
    assert versions_mod.delete_note_versions(None, [None, ""]) == 0
    assert shared_note_ids(None, []) == set()


@pytest.mark.parametrize("raw", [None, "", "{oops", '"text"', '{"before": "not a dict", "after": "x"}', '{"before": {}, "after": ""}'])
def test_a_damaged_undo_slot_offers_no_undo(raw):
    assert parse_undo(raw) is None


def test_what_runs_last_read_is_bounded(monkeypatch):
    monkeypatch.setattr(scope_mod, "_SEEN_BY_SESSION", type(scope_mod._SEEN_BY_SESSION)())
    monkeypatch.setattr(scope_mod, "_SEEN_BY_SESSION_MAX", 2)
    stamp = datetime.datetime(2026, 11, 13, 1, 0)
    for sid in ("a", "b", "c"):
        NoteEditScope(session_id=sid, note_id="n").saw(Note(id="n", updated_at=stamp))
    assert list(scope_mod._SEEN_BY_SESSION) == ["b", "c"], "the oldest run is forgotten first"
    # Another note, or a note with no version yet, records nothing.
    NoteEditScope(session_id="d", note_id="n").saw(Note(id="other", updated_at=stamp))
    NoteEditScope(session_id="e", note_id="n").saw(Note(id="n", updated_at=None))
    assert list(scope_mod._SEEN_BY_SESSION) == ["b", "c"]


@pytest.fixture
def factory(tmp_path):
    with disposable_database(tmp_path) as make:
        with make() as db:
            db.add(Note(id="n1", owner="alice", title="Trip", content="x"))
            db.add(DbSession(id="chat", name="Chat", endpoint_url="", model="m", owner="alice"))
            db.commit()
        yield make


def test_a_scope_needs_a_chat_and_an_unknown_chat_cannot_be_scoped(factory):
    with factory() as db:
        assert load_note_edit_scope(db, None, "alice") is None
        assert load_note_edit_scope(db, "chat", "alice") is None, "an ordinary chat"
        with pytest.raises(LookupError):
            set_session_note_scope(db, "no-such-chat", "n1")
        set_session_note_scope(db, "chat", "n1", "")
        scope = load_note_edit_scope(db, "chat", "alice")
        assert (scope.note_id, scope.shared, scope.focus_task) == ("n1", False, "")


def test_a_write_that_loses_every_retry_gives_up(factory, monkeypatch):
    def always_lose(db, note, stamp):
        db.rollback()
        raise NoteChanged(note.id)
    monkeypatch.setattr("core.note_writes.commit_if_unchanged", always_lose)
    attempts = []
    with factory() as db:
        with pytest.raises(NoteChanged):
            write_with_retry(db, "n1", lambda: db.get(Note, "n1"), lambda note: attempts.append(1), attempts=3)
    assert len(attempts) == 3
