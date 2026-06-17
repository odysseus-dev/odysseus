from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_serialize_ignores_invalid_note_items(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = SimpleNamespace(
        id="n1",
        title="Checklist",
        content="",
        items="{bad json",
        note_type="checklist",
        color=None,
        label=None,
        pinned=False,
        archived=False,
        due_date=None,
        source=None,
        created_at=None,
        updated_at=None,
    )

    assert cli._serialize(note)["items"] == []


def test_serialize_keeps_list_note_items(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = SimpleNamespace(
        id="n1",
        title="Checklist",
        content="",
        items='[{"text": "done"}]',
        note_type="checklist",
        color=None,
        label=None,
        pinned=False,
        archived=False,
        due_date=None,
        source=None,
        created_at=None,
        updated_at=None,
    )

    assert cli._serialize(note)["items"] == [{"text": "done"}]
