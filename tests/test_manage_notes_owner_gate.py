"""manage_notes owner gate, against a disposable real database.

The lookup itself is owner-scoped (owned or shared-with-you notes only), so a
legacy null/empty-owner row or another account's note is "not found" with the
same message as an id that does not exist.
"""
import asyncio
import json

import pytest

import core.database as cdb
from core.database import Note
from src import tool_implementations
from tests.helpers.database import disposable_database

_ID = "abc12345-existing"


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)

        def seed(owner=None, **overrides):
            data = {
                "id": _ID,
                "owner": owner,
                "title": "Original",
                "content": "",
                "note_type": "note",
                "items": '[{"text":"item","done":false}]',
            }
            data.update(overrides)
            with factory() as db:
                db.add(Note(**data))
                db.commit()

        def row():
            with factory() as db:
                return db.get(Note, _ID)

        yield seed, row


def _run(args, owner="alice"):
    return asyncio.run(tool_implementations.do_manage_notes(json.dumps(args), owner=owner))


def test_update_rejects_legacy_null_owner_for_authenticated_owner(notes):
    seed, row = notes
    seed(owner=None)

    result = _run({"action": "update", "id": "abc12345", "title": "Changed"})

    assert result == {"error": "Note 'abc12345' not found", "exit_code": 1}
    assert row().title == "Original"


def test_delete_rejects_legacy_empty_owner_for_authenticated_owner(notes):
    seed, row = notes
    seed(owner="")

    result = _run({"action": "delete", "id": "abc12345"})

    assert result == {"error": "Note 'abc12345' not found", "exit_code": 1}
    assert row() is not None


def test_toggle_rejects_other_owner(notes):
    seed, row = notes
    seed(owner="bob")

    result = _run({"action": "toggle_item", "id": "abc12345", "index": 0})

    assert result == {"error": "Note 'abc12345' not found", "exit_code": 1}
    assert "[x]" not in (row().content or "") and json.loads(row().items)[0]["done"] is False


def test_other_owner_and_missing_id_get_the_same_error(notes):
    seed, _row = notes
    seed(owner="bob")
    assert _run({"action": "view", "id": "abc12345"}) == {"error": "Note 'abc12345' not found", "exit_code": 1}
    assert _run({"action": "view", "id": "zzz99999"}) == {"error": "Note 'zzz99999' not found", "exit_code": 1}


def test_ablation_scope_blocks_nonfixture_even_with_matching_owner(notes):
    from src.tool_routing_experiment import note_fixture_scope
    seed, row = notes
    seed(owner="alice")
    token = note_fixture_scope.set(frozenset({'different-fixture'}))
    try:
        result = _run({'action': 'delete', 'id': _ID})
    finally:
        note_fixture_scope.reset(token)
    assert result['exit_code'] == 1
    assert row() is not None


def test_ablation_scope_allows_owned_fixture(notes):
    from src.tool_routing_experiment import note_fixture_scope
    seed, row = notes
    seed(owner="alice")
    token = note_fixture_scope.set(frozenset({_ID}))
    try:
        result = _run({'action': 'delete', 'id': _ID})
    finally:
        note_fixture_scope.reset(token)
    assert result['exit_code'] == 0
    assert row() is None


def test_update_allows_matching_owner(notes):
    seed, row = notes
    seed(owner="alice")

    result = _run({"action": "update", "id": "abc12345", "title": "Changed"})

    assert result["exit_code"] == 0
    assert row().title == "Changed"


def test_view_returns_full_freeform_note_content(notes):
    seed, _row = notes
    content = "First line. " + ("complete detail " * 10) + "FINAL_MARKER"
    seed(owner="alice", title="Long note", content=content, items=None)

    result = _run({"action": "view", "id": "abc12345"})

    assert result["exit_code"] == 0
    assert content in result["results"]
    assert "FINAL_MARKER" in result["results"]
