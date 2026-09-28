"""The agent's note tool must write back to To Do like the HTTP routes do.

`src/tools/notes.py` writes to the notes table directly rather than going
through `/api/notes`, so it needs its own write-back. Without it a task the
agent added or ticked off would exist only in Odysseus — the split-brain the
sync exists to prevent, and exactly the gap the calendar tools had.

The HTTP routes hand the push to BackgroundTasks; the agent has no response
to hang one off, so it awaits the same write-back directly.
"""

import asyncio
import json
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import MsTodoDeletedNote, Note
from src import msgraph_todo as mt
from src.tools.notes import do_manage_notes

OWNER = "alice"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Temp notes DB with the Graph push intercepted. Returns (factory, pushes)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notes.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cdb, "SessionLocal", factory)

    pushes = []

    def _record(action):
        async def _push(owner, note_id):
            pushes.append((owner, note_id, action))
            return {"ok": True}
        return _push

    monkeypatch.setattr(mt, "push_task_create", _record("create"))
    monkeypatch.setattr(mt, "push_task_update", _record("update"))
    monkeypatch.setattr(mt, "push_task_delete", _record("delete"))
    return factory, pushes


def _run(args, owner=OWNER):
    return asyncio.run(do_manage_notes(json.dumps(args), owner))


def _seed(factory, **over):
    fields = {
        "id": str(uuid.uuid4()), "owner": OWNER, "title": "task",
        "note_type": "todo",
    }
    fields.update(over)
    db = factory()
    try:
        db.add(Note(**fields))
        db.commit()
    finally:
        db.close()
    return fields["id"]


def _get(factory, note_id):
    db = factory()
    try:
        return db.query(Note).filter(Note.id == note_id).first()
    finally:
        db.close()


def test_a_task_the_agent_adds_reaches_to_do(env):
    factory, pushes = env
    out = _run({"action": "add", "title": "Call the dentist", "note_type": "todo"})
    assert out["exit_code"] == 0
    assert [p[2] for p in pushes] == ["create"]
    assert pushes[0][1] == out["note_id"]


def test_a_checklist_the_agent_adds_reaches_to_do(env):
    """`checklist` is what the tool stores when the model supplies items."""
    factory, pushes = env
    out = _run({"action": "add", "title": "Shopping",
                "checklist_items": [{"text": "milk", "done": False}]})
    assert out["exit_code"] == 0
    assert _get(factory, out["note_id"]).note_type == "checklist"
    assert [p[2] for p in pushes] == ["create"]


def test_a_plain_note_the_agent_adds_stays_local(env):
    _, pushes = env
    out = _run({"action": "add", "title": "Random thought", "content": "hmm"})
    assert out["exit_code"] == 0
    assert pushes == []


def test_the_pending_marker_is_written_with_the_note(env):
    """So a crash between the commit and the push still leaves the retry."""
    factory, _ = env
    out = _run({"action": "add", "title": "Call the dentist", "note_type": "todo"})
    assert _get(factory, out["note_id"]).todo_sync_pending == "create"


def test_an_edit_by_the_agent_reaches_to_do(env):
    factory, pushes = env
    note_id = _seed(factory, remote_id="T1", remote_list_id="L1")
    out = _run({"action": "update", "id": note_id, "title": "Renamed"})
    assert out["exit_code"] == 0
    assert pushes == [(OWNER, note_id, "update")]


def test_the_agent_ticking_an_item_reaches_to_do(env):
    factory, pushes = env
    note_id = _seed(factory, remote_id="T1", remote_list_id="L1",
                    items=json.dumps([{"text": "milk", "done": False}]))
    out = _run({"action": "toggle_item", "id": note_id, "index": 0})
    assert out["exit_code"] == 0
    assert pushes == [(OWNER, note_id, "update")]


def test_the_agent_deleting_a_task_removes_it_upstream(env):
    factory, pushes = env
    note_id = _seed(factory, remote_id="T1", remote_list_id="L1", todo_account_id="a1")
    out = _run({"action": "delete", "id": note_id})
    assert out["exit_code"] == 0
    assert pushes == [(OWNER, note_id, "delete")]
    db = factory()
    try:
        tomb = db.query(MsTodoDeletedNote).filter(MsTodoDeletedNote.id == note_id).first()
        assert tomb is not None and tomb.remote_id == "T1"
    finally:
        db.close()


def test_deleting_a_task_that_never_reached_graph_pushes_nothing(env):
    factory, pushes = env
    note_id = _seed(factory)
    _run({"action": "delete", "id": note_id})
    assert pushes == []


def test_a_plain_note_edit_by_the_agent_pushes_nothing(env):
    factory, pushes = env
    note_id = _seed(factory, note_type="note")
    _run({"action": "update", "id": note_id, "title": "Renamed"})
    assert pushes == []


def test_a_failing_push_never_loses_the_agents_write(env, monkeypatch):
    """Local SQLite stays authoritative; Graph being down is not a reason to
    fail the tool call."""
    factory, _ = env

    async def _boom(owner, note_id):
        raise RuntimeError("graph unreachable")

    monkeypatch.setattr(mt, "push_task_create", _boom)
    out = _run({"action": "add", "title": "Still saved", "note_type": "todo"})
    assert out["exit_code"] == 0
    assert _get(factory, out["note_id"]).title == "Still saved"
