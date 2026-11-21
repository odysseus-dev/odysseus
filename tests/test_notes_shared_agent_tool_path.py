"""A collaborator's own agent reaches a note shared with them through the real
tool path (tool_execution -> owned resource binding -> manage_notes), with the
share's permission: view reads, edit writes, owner-only fields stay refused."""
import json

import pytest

import core.database as cdb
from core.database import Note, NoteShare
from src import tool_execution as execution
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.tools import notes as notes_tool
from src.tool_types import ToolBlock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

VIEW = "view0001-shared-note"
EDIT = "edit0002-shared-note"
PRIVATE = "priv0003-alice-only"
BOBS = "edit0004-bobs-own"


@pytest.fixture
def db(monkeypatch):
    import src.database as compatibility
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    try:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(compatibility, "SessionLocal", factory)
        monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: False)
        monkeypatch.setattr(notes_tool, "_SEEN_VERSIONS", type(notes_tool._SEEN_VERSIONS)())
        with factory() as s:
            s.add(cdb.Session(id="bob-chat", owner="bob", name="chat", endpoint_url="https://model.test", model="m"))
            s.add(Note(id=VIEW, owner="alice", title="Read me", content="view body"))
            s.add(Note(id=EDIT, owner="alice", title="Plan", content="edit body"))
            s.add(Note(id=PRIVATE, owner="alice", title="Diary", content="private body"))
            s.add(Note(id=BOBS, owner="bob", title="Plan", content="bob body"))
            s.add(NoteShare(id="s1", note_id=VIEW, shared_with="bob", permission="view"))
            s.add(NoteShare(id="s2", note_id=EDIT, shared_with="bob", permission="edit"))
            s.commit()
        yield factory
    finally:
        engine.dispose()


async def _bob(args):
    authority = RequestAuthority("bob-request", "bob", "bob-chat", "", (OperationGrant("manage_notes"),))
    _, result = await execution.execute_tool_block(
        ToolBlock("manage_notes", json.dumps(args)), owner="bob", session_id="bob-chat",
        request_authority=authority, security_context=execution.NO_TOOL_SECURITY_CONTEXT)
    return result


def _note(factory, note_id):
    with factory() as s:
        return s.get(Note, note_id)


async def test_view_share_reads_and_refuses_a_write(db):
    read = await _bob({"action": "view", "id": VIEW})
    assert read["exit_code"] == 0, read
    assert "view body" in read["results"]
    write = await _bob({"action": "update", "id": VIEW, "content": "changed"})
    assert write.get("failure_kind") != "resource_identity_denied", write
    assert write["exit_code"] == 1 and "view only" in write["error"]
    assert _note(db, VIEW).content == "view body"


async def test_edit_share_writes_through_the_tool_path(db):
    assert (await _bob({"action": "view", "id": EDIT}))["exit_code"] == 0
    result = await _bob({"action": "update", "id": EDIT, "content": "bob was here"})
    assert result["exit_code"] == 0, result
    assert _note(db, EDIT).content == "bob was here"
    assert _note(db, EDIT).owner == "alice"


@pytest.mark.parametrize("args", [
    {"action": "update", "id": EDIT, "archived": True},
    {"action": "update", "id": EDIT, "pinned": True},
    {"action": "delete", "id": EDIT},
])
async def test_edit_share_keeps_owner_only_changes_refused(db, args):
    result = await _bob(args)
    assert result["exit_code"] == 1, result
    note = _note(db, EDIT)
    assert (note.archived, note.pinned, note.content) == (False, False, "edit body")


async def test_a_note_not_shared_stays_unreachable(db):
    for args in ({"action": "view", "id": PRIVATE}, {"action": "view", "id": "priv0003"},
                 {"action": "view", "title": "Diary"}):
        result = await _bob(args)
        assert result["failure_kind"] == "resource_identity_denied", result
        assert "private body" not in json.dumps(result)


async def test_a_shared_note_never_shadows_the_callers_own(db):
    # Both "Plan" notes match the title and the "edit000" prefix; bob's own wins.
    for args in ({"action": "view", "title": "Plan"}, {"action": "view", "id": "edit000"}):
        result = await _bob(args)
        assert result["exit_code"] == 0, result
        assert "bob body" in result["results"] and "edit body" not in result["results"]
    # Only alice's shared note matches this prefix, so it resolves to it.
    result = await _bob({"action": "view", "id": "view000"})
    assert result["exit_code"] == 0 and "view body" in result["results"]


async def test_a_revoked_share_cannot_be_used(db):
    with db() as s:
        s.query(NoteShare).filter(NoteShare.note_id == EDIT).delete()
        s.commit()
    result = await _bob({"action": "update", "id": EDIT, "content": "late"})
    assert result["failure_kind"] == "resource_identity_denied"
    assert _note(db, EDIT).content == "edit body"


def _dress(factory, note_id):
    """Give the owner's note a value in every owner-only field the tool reads back."""
    with factory() as s:
        note = s.get(Note, note_id)
        note.color, note.label, note.due_date = "yellow", "work", "2026-12-01T09:00:00+00:00"
        note.pinned, note.note_type = True, "checklist"
        s.commit()


def _owner_fields(factory, note_id):
    note = _note(factory, note_id)
    return (note.color, note.label, note.due_date, note.pinned, note.archived, note.note_type)


async def test_edit_share_can_send_back_owner_only_fields_unchanged(db):
    # The collaborator's agent views the note and sends it back whole: the
    # owner-only keys carry what is stored, so only the content changes.
    _dress(db, EDIT)
    before = _owner_fields(db, EDIT)
    result = await _bob({"action": "update", "id": EDIT, "content": "bob was here",
                         "color": "yellow", "label": "work", "due_date": "2026-12-01T09:00:00+00:00",
                         "pinned": True, "archived": False, "note_type": "checklist"})
    assert result["exit_code"] == 0, result
    assert _note(db, EDIT).content == "bob was here"
    assert _owner_fields(db, EDIT) == before


async def test_edit_share_null_matching_an_empty_field_is_unchanged(db):
    result = await _bob({"action": "update", "id": EDIT, "content": "bob was here",
                         "color": None, "label": None, "due_date": None})
    assert result["exit_code"] == 0, result
    assert _note(db, EDIT).content == "bob was here"
    assert _owner_fields(db, EDIT)[:3] == (None, None, None)


@pytest.mark.parametrize("field,value", [
    ("color", "green"), ("color", None), ("label", "home"), ("label", None),
    ("due_date", "2026-12-02T09:00:00+00:00"), ("due_date", None),
    ("pinned", False), ("pinned", 1), ("pinned", None), ("archived", True), ("archived", None),
    ("note_type", "note"),
])
async def test_edit_share_still_refused_when_one_owner_only_value_differs(db, field, value):
    _dress(db, EDIT)
    before = _owner_fields(db, EDIT)
    args = {"action": "update", "id": EDIT, "content": "late", "color": "yellow", "label": "work",
            "due_date": "2026-12-01T09:00:00+00:00", "pinned": True, "archived": False,
            "note_type": "checklist"}
    args[field] = value
    result = await _bob(args)
    assert result["exit_code"] == 1, result
    assert "owner" in result["error"] and result["error"].count(field) == 1
    blocked = result["error"].split("change: ", 1)[1].split(".", 1)[0]
    assert blocked == field, result["error"]
    assert _note(db, EDIT).content == "edit body"
    assert _owner_fields(db, EDIT) == before
