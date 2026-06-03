import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    db_stub = types.ModuleType("core.database")
    db_stub.SessionLocal = MagicMock()
    db_stub.Note = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db_stub)

    path = ROOT / "scripts" / "odysseus-notes"
    loader = importlib.machinery.SourceFileLoader("odysseus_notes_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_serialize_ignores_invalid_note_items(monkeypatch):
    cli = _load_cli(monkeypatch)
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
    cli = _load_cli(monkeypatch)
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
