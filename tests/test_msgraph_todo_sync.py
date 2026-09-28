"""Database-level behaviour of the Microsoft To Do sync.

These drive `_sync_account_blocking` and the write-back against a real
SQLite database with `_graph_paged` stubbed, because the rules worth pinning
are about what is written and — more importantly — what is *not*:

- an unchanged task must not be rewritten, or `updated_at` moves and the
  notes board, which is ordered by it, reshuffles on every sync;
- a local edit that has not reached Graph outranks the server copy;
- pruning only ever touches rows this sync owns, and never runs after a walk
  that failed or stopped at the page cap.
"""

import json
import tempfile
import time
import unittest.mock as mock
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import MsTodoDeletedNote, Note
from src import msgraph_todo as mt

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)

OWNER = "tester"
ACCOUNT = "acc-1"
LIST_ID = "L1"


@pytest.fixture(autouse=True)
def _use_test_db(monkeypatch):
    """Point the sync at this module's DB, and start each test empty.

    Scoped rather than assigned at import: another test module binds the same
    attribute to its own engine, and whichever imported last would win for
    the whole session otherwise.
    """
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    db = _TS()
    try:
        db.query(Note).delete()
        db.query(MsTodoDeletedNote).delete()
        db.commit()
    finally:
        db.close()


def _task(remote_id="T1", **over):
    base = {
        "id": remote_id,
        "@odata.etag": 'W/"v1"',
        "title": "Buy milk",
        "body": {"contentType": "text", "content": ""},
        "status": "notStarted",
        "importance": "normal",
        "lastModifiedDateTime": "2026-09-20T10:00:00.0000000Z",
        "checklistItems": [],
    }
    base.update(over)
    return base


def _stub_graph(monkeypatch, tasks, *, lists=None, truncated=False, tasks_fail=False):
    """Stand in for Graph: one account, one list, the given tasks."""
    lists = lists if lists is not None else [
        {"id": LIST_ID, "displayName": "Tasks", "wellknownListName": "defaultList"},
    ]

    def _paged(token, path, params=None):
        if path == "/me/todo/lists":
            return list(lists), False
        if tasks_fail:
            raise mt.GraphError(503, "service unavailable")
        return list(tasks), truncated

    monkeypatch.setattr(mt, "_graph_paged", _paged)


def _sync():
    return mt._sync_account_blocking(OWNER, ACCOUNT, "tok")


def _notes():
    db = _TS()
    try:
        return db.query(Note).all()
    finally:
        db.close()


def _add_note(**over):
    fields = {
        "id": str(uuid.uuid4()),
        "owner": OWNER,
        "title": "local",
        "note_type": "todo",
        "origin": "mstodo",
        "remote_id": "T-old",
        "remote_list_id": LIST_ID,
        "todo_account_id": ACCOUNT,
    }
    fields.update(over)
    db = _TS()
    try:
        note = Note(**fields)
        db.add(note)
        db.commit()
        return fields["id"]
    finally:
        db.close()


# ── Pull ──────────────────────────────────────────────────────────

def test_a_task_arrives_as_a_todo_note(monkeypatch):
    _stub_graph(monkeypatch, [_task()])
    result = _sync()
    notes = _notes()
    assert result["tasks"] == 1 and len(notes) == 1
    assert notes[0].title == "Buy milk"
    assert notes[0].note_type == "todo"
    assert notes[0].origin == "mstodo"
    assert notes[0].remote_list_id == LIST_ID
    assert notes[0].todo_account_id == ACCOUNT


def test_the_same_task_pulled_twice_produces_one_note(monkeypatch):
    _stub_graph(monkeypatch, [_task()])
    _sync()
    _sync()
    assert len(_notes()) == 1


def test_an_unchanged_task_does_not_touch_updated_at(monkeypatch):
    """The active notes view is ordered by updated_at, so rewriting an
    unchanged row would reshuffle the user's board on every sync."""
    _stub_graph(monkeypatch, [_task()])
    _sync()
    before = _notes()[0].updated_at
    time.sleep(0.01)
    _sync()
    assert _notes()[0].updated_at == before


def test_a_changed_task_is_written_through(monkeypatch):
    _stub_graph(monkeypatch, [_task()])
    _sync()
    _stub_graph(monkeypatch, [_task(title="Buy oat milk", **{"@odata.etag": 'W/"v2"'})])
    _sync()
    assert _notes()[0].title == "Buy oat milk"


def test_a_moved_etag_alone_does_not_reshuffle_the_board(monkeypatch):
    """The etag changes whenever the server touches the task, including for
    things we do not mirror."""
    _stub_graph(monkeypatch, [_task()])
    _sync()
    before = _notes()[0].updated_at
    time.sleep(0.01)
    _stub_graph(monkeypatch, [_task(**{"@odata.etag": 'W/"v9"'})])
    _sync()
    assert _notes()[0].updated_at == before


def test_checklist_items_round_trip_into_the_note(monkeypatch):
    _stub_graph(monkeypatch, [_task(checklistItems=[{"displayName": "2%", "isChecked": True}])])
    _sync()
    assert json.loads(_notes()[0].items) == [{"text": "2%", "done": True}]


def test_a_pending_local_edit_outranks_the_server_copy(monkeypatch):
    note_id = _add_note(remote_id="T1", title="mine", todo_sync_pending="update")
    _stub_graph(monkeypatch, [_task(title="theirs")])
    _sync()
    db = _TS()
    try:
        assert db.query(Note).filter(Note.id == note_id).first().title == "mine"
    finally:
        db.close()


def test_a_tag_added_here_survives_the_pull(monkeypatch):
    lists = [{"id": LIST_ID, "displayName": "Groceries"}]
    _stub_graph(monkeypatch, [_task()], lists=lists)
    _sync()
    db = _TS()
    try:
        note = db.query(Note).first()
        note.label = "groceries urgent"
        db.commit()
    finally:
        db.close()
    _stub_graph(monkeypatch, [_task(title="Buy oat milk")], lists=lists)
    _sync()
    assert set(mt.note_tags(_notes()[0].label)) == {"groceries", "urgent"}


def test_a_long_completed_task_is_left_upstream(monkeypatch):
    """A list keeps its completed tasks forever; importing all of them would
    grow the local Archive without bound."""
    _stub_graph(monkeypatch, [_task(
        status="completed", lastModifiedDateTime="2020-01-01T00:00:00.0000000Z",
    )])
    _sync()
    assert _notes() == []


def test_a_recently_completed_task_still_arrives_archived(monkeypatch):
    from datetime import datetime, timedelta

    recent = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    _stub_graph(monkeypatch, [_task(status="completed", lastModifiedDateTime=recent)])
    _sync()
    assert _notes()[0].archived is True


# ── Pruning ───────────────────────────────────────────────────────

def test_a_task_deleted_upstream_takes_its_note_with_it(monkeypatch):
    _add_note(remote_id="T-gone")
    _stub_graph(monkeypatch, [_task(remote_id="T1")])
    result = _sync()
    assert result["deleted"] == 1
    assert [n.remote_id for n in _notes()] == ["T1"]


def test_emptying_the_list_upstream_clears_the_notes(monkeypatch):
    """A 200 with no tasks is a real answer — a failed fetch is reported
    separately — so ticking off the last task on a phone must land here."""
    _add_note(remote_id="T-gone")
    _stub_graph(monkeypatch, [])
    assert _sync()["deleted"] == 1
    assert _notes() == []


def test_a_purely_local_note_is_never_pruned(monkeypatch):
    _add_note(origin=None, remote_id=None, remote_list_id=None, todo_account_id=None)
    _stub_graph(monkeypatch, [_task()])
    _sync()
    assert any(n.remote_id is None for n in _notes())


def test_a_note_with_an_unpushed_edit_is_never_pruned(monkeypatch):
    _add_note(remote_id="T-gone", todo_sync_pending="update")
    _stub_graph(monkeypatch, [_task()])
    assert _sync()["deleted"] == 0


def test_another_accounts_notes_are_left_alone(monkeypatch):
    _add_note(remote_id="T-gone", todo_account_id="other-account")
    _stub_graph(monkeypatch, [_task()])
    assert _sync()["deleted"] == 0


def test_a_failed_fetch_prunes_nothing(monkeypatch):
    """An error means "we could not look", not "the list is empty"."""
    _add_note(remote_id="T-gone")
    _stub_graph(monkeypatch, [], tasks_fail=True)
    result = _sync()
    assert result["deleted"] == 0
    assert result["errors"]
    assert len(_notes()) == 1


def test_a_truncated_walk_prunes_nothing(monkeypatch):
    """Stopping at the page cap means the rest of the list was never seen."""
    _add_note(remote_id="T-gone")
    _stub_graph(monkeypatch, [_task()], truncated=True)
    assert _sync()["deleted"] == 0
    assert len(_notes()) == 2


def test_a_list_that_cannot_be_read_does_not_abort_the_others(monkeypatch):
    calls = {"n": 0}
    lists = [
        {"id": "L1", "displayName": "Broken"},
        {"id": "L2", "displayName": "Fine"},
    ]

    def _paged(token, path, params=None):
        if path == "/me/todo/lists":
            return list(lists), False
        calls["n"] += 1
        if "L1" in path:
            raise mt.GraphError(500, "boom")
        return [_task(remote_id="T2")], False

    monkeypatch.setattr(mt, "_graph_paged", _paged)
    result = _sync()
    assert result["errors"]
    assert result["tasks"] == 1


def test_an_unreadable_account_reports_rather_than_raising(monkeypatch):
    def _paged(token, path, params=None):
        raise mt.GraphError(403, "no consent for Tasks.ReadWrite")

    monkeypatch.setattr(mt, "_graph_paged", _paged)
    result = _sync()
    assert result["tasks"] == 0
    assert "no consent" in result["errors"][0]


# ── Write-back ────────────────────────────────────────────────────

def test_a_new_note_is_posted_into_the_chosen_list(monkeypatch):
    note_id = _add_note(
        origin=None, remote_id=None, remote_list_id=None, label="groceries",
    )
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mt, "_graph_paged",
                        lambda t, p, params=None: ([{"id": "LG", "displayName": "Groceries"}], False))
    monkeypatch.setattr(mt, "_sync_checklist_items", lambda *a, **k: None)
    request = mock.Mock(return_value={"id": "T-new", "@odata.etag": 'W/"v1"'})
    monkeypatch.setattr(mt, "_graph_request", request)

    assert mt._push_blocking(OWNER, note_id, "create")["ok"] is True
    assert request.call_args.args[1:3] == ("POST", "/me/todo/lists/LG/tasks")
    db = _TS()
    try:
        note = db.query(Note).filter(Note.id == note_id).first()
        assert note.remote_id == "T-new"
        assert note.remote_list_id == "LG"
        assert note.origin == "mstodo"
        assert note.todo_sync_pending is None
    finally:
        db.close()


def test_an_existing_task_is_patched_in_place(monkeypatch):
    note_id = _add_note(remote_id="T1")
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mt, "_sync_checklist_items", lambda *a, **k: None)
    request = mock.Mock(return_value={"id": "T1"})
    monkeypatch.setattr(mt, "_graph_request", request)

    assert mt._push_blocking(OWNER, note_id, "update")["ok"] is True
    assert request.call_args.args[1:3] == ("PATCH", f"/me/todo/lists/{LIST_ID}/tasks/T1")


def test_a_plain_note_is_never_pushed(monkeypatch):
    """Writing every scratch note into someone's task list would be a
    surprise, and none of those shapes survives the round trip."""
    note_id = _add_note(note_type="note", remote_id=None)
    out = mt._push_blocking(OWNER, note_id, "create")
    assert out["ok"] is True and out["skipped"]


def test_a_task_already_gone_upstream_is_not_an_error(monkeypatch):
    note_id = _add_note(remote_id="T1")
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mt, "_graph_request",
                        mock.Mock(side_effect=mt.GraphError(404, "not found")))
    assert mt._push_blocking(OWNER, note_id, "update")["ok"] is True


def test_a_server_error_is_reported(monkeypatch):
    note_id = _add_note(remote_id="T1")
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mt, "_graph_request",
                        mock.Mock(side_effect=mt.GraphError(500, "server blew up")))
    out = mt._push_blocking(OWNER, note_id, "update")
    assert out["ok"] is False and "server blew up" in out["error"]


def test_an_expired_sign_in_is_reported_not_retried_blindly(monkeypatch):
    note_id = _add_note(remote_id="T1")
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: None)
    out = mt._push_blocking(OWNER, note_id, "update")
    assert out["ok"] is False and "reconnect" in out["error"]


def test_a_failed_push_leaves_a_retry_marker(monkeypatch):
    """Local SQLite stays authoritative; the edit must not be lost because
    Graph was unreachable for a moment."""
    note_id = _add_note(remote_id="T1", todo_sync_pending=None)
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mt, "_graph_request",
                        mock.Mock(side_effect=mt.GraphError(500, "boom")))
    mt.push_note_blocking(OWNER, note_id, "update")
    db = _TS()
    try:
        assert db.query(Note).filter(Note.id == note_id).first().todo_sync_pending == "update"
    finally:
        db.close()


def test_a_delete_is_driven_by_the_tombstone(monkeypatch):
    """The note row is gone by the time the push runs, so the remote ids
    have nowhere else to live."""
    db = _TS()
    try:
        db.add(MsTodoDeletedNote(id="n1", owner=OWNER, remote_id="T1",
                                 remote_list_id=LIST_ID, account_id=ACCOUNT))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    request = mock.Mock(return_value={})
    monkeypatch.setattr(mt, "_graph_request", request)

    assert mt.push_note_blocking(OWNER, "n1", "delete")["ok"] is True
    assert request.call_args.args[1:3] == ("DELETE", f"/me/todo/lists/{LIST_ID}/tasks/T1")
    assert _TS().query(MsTodoDeletedNote).filter(MsTodoDeletedNote.id == "n1").first() is None


def test_a_failed_delete_keeps_the_tombstone_for_the_next_sync(monkeypatch):
    db = _TS()
    try:
        db.add(MsTodoDeletedNote(id="n1", owner=OWNER, remote_id="T1",
                                 remote_list_id=LIST_ID, account_id=ACCOUNT))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(mt, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mt, "_graph_request",
                        mock.Mock(side_effect=mt.GraphError(500, "boom")))
    assert mt.push_note_blocking(OWNER, "n1", "delete")["ok"] is False
    assert _TS().query(MsTodoDeletedNote).filter(MsTodoDeletedNote.id == "n1").first() is not None


# ── Checklist reconciliation ──────────────────────────────────────

def _capture_checklist(monkeypatch, existing):
    monkeypatch.setattr(mt, "_graph_paged", lambda t, p, params=None: (list(existing), False))
    calls = []

    def _request(token, method, path, **kw):
        calls.append((method, path, kw.get("json_body")))
        return {}

    monkeypatch.setattr(mt, "_graph_request", _request)
    return calls


def test_a_changed_step_is_patched_in_place(monkeypatch):
    """Graph does not accept checklistItems on a task PATCH — they are their
    own collection — so each step is reconciled by position."""
    calls = _capture_checklist(monkeypatch, [{"id": "c1", "displayName": "old", "isChecked": False}])
    mt._sync_checklist_items("tok", "L1", "T1", [{"text": "new", "done": True}])
    assert [c[0] for c in calls] == ["PATCH"]
    assert calls[0][1].endswith("/c1")
    assert calls[0][2] == {"displayName": "new", "isChecked": True}


def test_an_added_step_is_appended(monkeypatch):
    calls = _capture_checklist(monkeypatch, [{"id": "c1", "displayName": "same", "isChecked": False}])
    mt._sync_checklist_items("tok", "L1", "T1", [
        {"text": "same", "done": False}, {"text": "extra", "done": False},
    ])
    assert [c[0] for c in calls] == ["POST"]
    assert calls[0][2] == {"displayName": "extra", "isChecked": False}


def test_a_removed_step_is_deleted_upstream(monkeypatch):
    calls = _capture_checklist(monkeypatch, [
        {"id": "c1", "displayName": "keep", "isChecked": False},
        {"id": "c2", "displayName": "surplus", "isChecked": False},
    ])
    mt._sync_checklist_items("tok", "L1", "T1", [{"text": "keep", "done": False}])
    assert [c[0] for c in calls] == ["DELETE"]
    assert calls[0][1].endswith("/c2")


def test_a_blank_step_is_never_sent(monkeypatch):
    calls = _capture_checklist(monkeypatch, [])
    mt._sync_checklist_items("tok", "L1", "T1", [{"text": "   ", "done": False}])
    assert calls == []


def test_an_unchanged_checklist_item_is_left_alone(monkeypatch):
    existing = [{"id": "c1", "displayName": "same", "isChecked": True}]
    monkeypatch.setattr(mt, "_graph_paged", lambda t, p, params=None: (existing, False))
    request = mock.Mock(return_value={})
    monkeypatch.setattr(mt, "_graph_request", request)
    mt._sync_checklist_items("tok", "L1", "T1", [{"text": "same", "done": True}])
    request.assert_not_called()


# ── Pending queue ─────────────────────────────────────────────────

def test_a_task_that_never_reached_graph_is_queued():
    note_id = _add_note(origin=None, remote_id=None, remote_list_id=None)
    ids, _ = mt._pending_writeback_ids(OWNER)
    assert note_id in ids


def test_a_plain_note_is_not_queued():
    _add_note(note_type="note", origin=None, remote_id=None, remote_list_id=None)
    ids, _ = mt._pending_writeback_ids(OWNER)
    assert ids == []


def test_a_synced_task_with_nothing_pending_is_not_queued():
    _add_note(remote_id="T1", todo_sync_pending=None)
    ids, _ = mt._pending_writeback_ids(OWNER)
    assert ids == []


def test_a_single_user_install_still_finds_its_own_notes():
    """The notes routes store NULL for an anonymous owner; a sync that only
    matched on equality would be a silent no-op for exactly those installs."""
    note_id = _add_note(owner=None, origin=None, remote_id=None, remote_list_id=None)
    ids, _ = mt._pending_writeback_ids("")
    assert note_id in ids
